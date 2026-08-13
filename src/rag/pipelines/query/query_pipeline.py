"""
Pipeline de query (GraphRAG de metadados → SQL).

    pergunta ─┬─► dense_text_embedder  ─┐
              └─► sparse_text_embedder ─┴─► hybrid_retriever (acha os cards)
                                              └─► subgraph_retriever (expande o grafo)
                                                     ├─► prompt_builder ─► llm ─┐
                                                     └───────────────────► answer_builder

Passo 1 (Qdrant, híbrido denso+esparso): acha os cards de schema mais relevantes
para a pergunta. Passo 2 (Neo4j, Cypher): a partir desses nós, percorre
`SEMANTIC_JOIN` e monta o subgrafo de schema (tabelas, colunas e JOINs). Passo 3
(LLM OpenAI, saída estruturada): interpreta a pergunta (IR mínima) e escreve o
SQL. Passo 4 (answer_builder): formaliza explicação + SQL + referências.

Modelos: embeddings iguais aos da ingestão (Ollama gte-qwen2 denso + BM25
esparso — ADR-0009 "plano B" on-prem). Só a GERAÇÃO de SQL usa a OpenAI. A IR é
gerada junto do SQL numa única chamada estruturada: entrega o ponto de controle
inspecionável do ADR-0006/0007 sem uma chamada de LLM extra nem reescrever o
roteamento da recuperação (fica para quando houver volume real de perguntas).
"""
from haystack import Pipeline
from haystack.components.builders import ChatPromptBuilder
from haystack.dataclasses import ChatMessage

from rag.settings.configs import settings
from rag.components.embedders.ollama_text_embedder import OllamaTextEmbedderFactory
from rag.components.embedders.fastembed_sparse_text_embedder import FastembedSparseTextEmbedderFactory
from rag.components.vectorstore.qdrant import QdrantStoreFactory
from rag.components.retrievers.qdrant_hybrid_retriever import QdrantHybridRetrieverFactory
from rag.components.graphstore.neo4j import Neo4jGraphStoreFactory
from rag.components.retrievers.neo4j_subgraph_retriever import Neo4jSubgraphRetriever
from rag.components.generators.openai_generator import OpenAIChatGeneratorFactory
from rag.components.answer.sql_answer_builder import SqlAnswerBuilder

SYSTEM_PROMPT = """\
Você é um especialista em SQL (PostgreSQL) para o banco analítico `cidades` do \
GovHub. A partir de um subgrafo de schema recuperado, você gera SQL correto e \
explica o resultado, SEM inventar tabelas ou colunas.

Regras:
- Use APENAS as tabelas e colunas do schema fornecido.
- **ESCOLHA DA TABELA (leia antes de escrever o SQL).** É comum o schema trazer \
várias tabelas irmãs sobre o mesmo assunto, diferindo no NÍVEL DE AGREGAÇÃO. \
Escolha comparando o que a pergunta pede com a linha "Grão:" e com a descrição \
de cada tabela:
  1. Se a pergunta pede um recorte agregado (por UF, por estado, por região, por \
período) e existe tabela cujo grão JÁ É esse recorte, use-a — não agregue a \
tabela de detalhe para chegar no mesmo lugar.
  2. Se a pergunta é sobre um empreendimento individual (ficha, contrato, \
situação de um caso), prefira a tabela cuja descrição diz ser a ficha/visão \
individual.
  3. Se a pergunta pede série ao longo do tempo, prefira a tabela cujo grão \
inclui mês/período.
  4. Na dúvida entre duas, prefira a que a descrição declara servir a esse uso, \
e diga na explicação por que escolheu.
- Refira tabelas no SQL pelo nome físico `schema.tabela` (o que aparece em \
"Tabela:"), NUNCA pelo FQN do catálogo entre parênteses.
- Para conectar tabelas, use SOMENTE os JOINs listados em "Relações de JOIN". \
JOINs marcados como "requer normalização" não casam diretamente — se precisar \
deles, diga na explicação que as chaves exigem normalização e evite afirmar um \
resultado exato.
- **GRÃO**: quando a tabela trouxer a linha "Grão:", respeite-a. Se ela avisar \
que há LINHAS DUPLICADAS, NUNCA agregue direto — deduplique primeiro pela chave \
do grão e agregue sobre o resultado. Ex.: em vez de \
`SELECT uf, SUM(x) FROM t GROUP BY uf`, use \
`SELECT uf, SUM(x) FROM (SELECT DISTINCT chave, uf, x FROM t) d GROUP BY uf`. \
Isso vale para SUM, COUNT, AVG e para rankings. Diga na explicação que houve \
deduplicação.
- Se o schema recuperado não tiver o necessário, NÃO invente: explique o que \
falta.
- Antes do SQL, interprete a pergunta numa IR mínima em conceitos de NEGÓCIO \
(não nomes físicos): `intent` (aggregate, count, list, compare, trend, …), \
`metric` (nome de negócio ou null) e `filters` (lista de {concept, value}).

Responda em JSON válido, exatamente com este formato:
{
  "explanation": "<explicação curta em português, 1-3 frases>",
  "sql": "<a query SQL, ou string vazia se não for possível>",
  "ir": {"intent": "<...>", "metric": <"..." ou null>,
         "filters": [{"concept": "<...>", "value": "<...>"}]},
  "tables_used": ["schema.tabela", "..."]
}\
"""

USER_PROMPT = """\
# Schema disponível (subgrafo recuperado)
{% for doc in documents %}
{{ doc.content }}
{% endfor %}

# Relações de JOIN disponíveis
{% if joins %}{% for j in joins %}
- {{ j.left_table }}.{{ j.left_col }} = {{ j.right_table }}.{{ j.right_col }} \
({% if j.sql_join_eligible %}JOIN direto{% else %}requer normalização{% endif %}, \
regra={{ j.join_rule }}, cardinalidade={{ j.cardinality }})
{% endfor %}{% else %}
(nenhuma relação de JOIN entre as tabelas recuperadas)
{% endif %}

# Pergunta do usuário
{{ query }}
"""


def build_query_pipeline(collection: str | None = None, top_k: int = 5) -> Pipeline:
    pipeline = Pipeline()

    # Embeddings da query (iguais à ingestão: denso Ollama + esparso BM25)
    pipeline.add_component("dense_text_embedder", OllamaTextEmbedderFactory().build())
    pipeline.add_component("sparse_text_embedder", FastembedSparseTextEmbedderFactory().build())

    # Passo 1: busca híbrida no Qdrant
    qdrant_store = QdrantStoreFactory(index=collection or settings.QDRANT_COLLECTION).build()
    pipeline.add_component("hybrid_retriever", QdrantHybridRetrieverFactory(qdrant_store, top_k=top_k).build())

    # Passo 1b (opcional): rerank cross-encoder do pool antes do corte.
    if settings.RERANK_ENABLED:
        from rag.components.retrievers.reranker import FastembedReranker
        pipeline.add_component("reranker", FastembedReranker(top_k=top_k))

    # Passo 2: expansão do subgrafo no Neo4j (SEMANTIC_JOIN + HAS_COLUMN)
    graph_store = Neo4jGraphStoreFactory().build()
    pipeline.add_component("subgraph_retriever", Neo4jSubgraphRetriever(graph_store=graph_store))

    # Passo 3: geração do SQL (OpenAI, saída estruturada em JSON)
    prompt_builder = ChatPromptBuilder(
        template=[
            ChatMessage.from_system(SYSTEM_PROMPT),
            ChatMessage.from_user(USER_PROMPT),
        ],
        required_variables=["documents", "query"],
        variables=["documents", "joins", "query"],
    )
    pipeline.add_component("prompt_builder", prompt_builder)
    pipeline.add_component("llm", OpenAIChatGeneratorFactory().build())

    # Passo 4: formalização da resposta (explicação + SQL + referências)
    pipeline.add_component("answer_builder", SqlAnswerBuilder())

    # Conexões
    pipeline.connect("dense_text_embedder.embedding", "hybrid_retriever.query_embedding")
    pipeline.connect("sparse_text_embedder.sparse_embedding", "hybrid_retriever.query_sparse_embedding")
    if settings.RERANK_ENABLED:
        pipeline.connect("hybrid_retriever.documents", "reranker.documents")
        pipeline.connect("reranker.documents", "subgraph_retriever.documents")
    else:
        pipeline.connect("hybrid_retriever.documents", "subgraph_retriever.documents")
    pipeline.connect("subgraph_retriever.documents", "prompt_builder.documents")
    pipeline.connect("subgraph_retriever.joins", "prompt_builder.joins")
    pipeline.connect("prompt_builder.prompt", "llm.messages")
    pipeline.connect("llm.replies", "answer_builder.replies")
    pipeline.connect("subgraph_retriever.documents", "answer_builder.documents")
    pipeline.connect("subgraph_retriever.joins", "answer_builder.joins")

    return pipeline
