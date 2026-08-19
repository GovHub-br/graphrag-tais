"""Pipeline de EXPLORAÇÃO — a Persona 1 (ADR-0014).

    pergunta ─┬─► dense_text_embedder  ─┐
              └─► sparse_text_embedder ─┴─► hybrid_retriever (top_k amplo)
                                              └─► catalog_retriever (Neo4j)
                                                     ├─► prompt_builder ─► llm ─┐
                                                     └───────────────────► answer_builder

Mesma primeira metade da pipeline da Persona 2 (embedding híbrido + Qdrant), com
duas diferenças deliberadas e um componente de grafo próprio:

- **`top_k` amplo** (`EXPLORE_TOP_K`, default 15 contra 5): quem explora precisa
  de cobertura, não de precisão sobre uma tabela.
- **`Neo4jCatalogRetriever`** no lugar do `Neo4jSubgraphRetriever`: expansão
  ampla (todos os vizinhos de 1 hop), ordenação por relevância e panorama de
  domínios por Cypher agregado.

Nada aqui toca a Persona 2: os componentes compartilhados são instanciados por
factory, com parâmetros próprios, e nenhum default de `settings` é alterado.
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
from rag.components.retrievers.neo4j_catalog_retriever import Neo4jCatalogRetriever
from rag.components.generators.openai_generator import OpenAIChatGeneratorFactory
from rag.components.answer.explore_answer_builder import ExploreAnswerBuilder

SYSTEM_PROMPT = """\
Você é um **guia do catálogo de dados** do GovHub. A pessoa que fala com você \
NÃO conhece as bases disponíveis: ela tem um assunto em mente e quer descobrir o \
que existe, o que cada base serve para responder e como começar a olhar o dado.

Você NÃO está medindo um indicador. Você está mostrando o território.

Regras:
- Cite APENAS tabelas que aparecem no catálogo fornecido, pelo nome físico \
`schema.tabela`. **NUNCA invente uma tabela** — quem pergunta não tem como \
conferir, e uma tabela inexistente vira uma busca perdida.
- O `summary` é o que a pessoa lê primeiro e, muitas vezes, só ele. Responda a \
pergunta **direto** ali: o que existe, como está organizado e por onde começar. \
Sem preâmbulo ("o catálogo oferece…") e sem repetir a lista de tabelas.
- **Se a pergunta for sobre o catálogo COMO UM TODO** ("quais bases vocês têm?", \
"o que dá para analisar aqui?"), responda pelo **panorama de domínios**, \
dizendo o que cada domínio cobre. As tabelas recuperadas, nesse caso, são só uma \
amostra do índice — não as apresente como se fossem "as bases disponíveis", e \
deixe `tables` vazio ou com no máximo duas de exemplo.
- Priorize as tabelas mais úteis para o assunto e explique **por que cada uma \
serve**, usando a linha "Grão:" e a descrição. O `why` é exibido numa **célula \
de tabela**: escreva UMA frase curta, de no máximo 15 palavras, sem repetir o \
nome da tabela. Quando duas tabelas forem irmãs (mesmo assunto, níveis de \
agregação diferentes), o `why` de cada uma deve dizer o que a separa da outra — \
essa distinção é o principal valor da sua resposta.
- Para cada tabela priorizada, proponha de 2 a 3 **perguntas concretas** que ela \
consegue responder, usando as colunas listadas. Perguntas específicas ("valor \
contratado por UF em 2024"), não genéricas ("analisar os dados").
- `sample_sql`: UMA consulta simples sobre UMA tabela, para a pessoa **ver o \
dado** — `SELECT` de poucas colunas com `LIMIT 20`, ou uma contagem por uma \
coluna categórica. Não é o SQL de um indicador. Use só colunas listadas.
- `gaps`: o que a pessoa provavelmente espera e **não está** no catálogo \
fornecido. Seja honesto e específico. Se tudo o que ela precisa existe, devolva \
lista vazia. Nunca liste como lacuna algo que está no catálogo.
- Português, tom direto.

Responda em JSON válido, exatamente com este formato:
{
  "summary": "<2 a 4 frases respondendo direto: o que existe e por onde começar>",
  "tables": [
    {"table": "schema.tabela",
     "why": "<uma frase curta, até 15 palavras — cabe numa célula de tabela>",
     "questions": ["<pergunta concreta>", "<outra>"]}
  ],
  "sample_sql": "<SELECT ... LIMIT 20>",
  "sample_sql_rationale": "<1 frase: o que essa amostra mostra>",
  "gaps": ["<o que não existe no catálogo e a pessoa pode esperar>"]
}\
"""

USER_PROMPT = """\
# Panorama do catálogo (domínios do escopo)
{% for d in domains %}
- {{ d.domain }} (`{{ d.schema }}`): {{ d.table_count }} tabelas
{% endfor %}

# Tabelas recuperadas para este assunto (em ordem de relevância)
{% if documents %}{% for doc in documents %}
{{ doc.content }}
{% endfor %}{% else %}
(nenhuma tabela específica recuperada — responda com o panorama dos domínios
acima, explicando o que cada domínio cobre, e peça um recorte mais específico)
{% endif %}

# Relações de JOIN entre as tabelas recuperadas
{% if joins %}{% for j in joins %}
- {{ j.left_table }}.{{ j.left_col }} = {{ j.right_table }}.{{ j.right_col }} \
({% if j.sql_join_eligible %}JOIN direto{% else %}requer normalização{% endif %})
{% endfor %}{% else %}
(nenhuma — as tabelas recuperadas não têm relação declarada entre si)
{% endif %}

# Pergunta do usuário
{{ query }}
"""


def build_explore_pipeline(collection: str | None = None, top_k: int | None = None) -> Pipeline:
    pipeline = Pipeline()
    top_k = top_k or settings.EXPLORE_TOP_K

    pipeline.add_component("dense_text_embedder", OllamaTextEmbedderFactory().build())
    pipeline.add_component("sparse_text_embedder", FastembedSparseTextEmbedderFactory().build())

    qdrant_store = QdrantStoreFactory(index=collection or settings.QDRANT_COLLECTION).build()
    pipeline.add_component(
        "hybrid_retriever", QdrantHybridRetrieverFactory(qdrant_store, top_k=top_k).build()
    )

    graph_store = Neo4jGraphStoreFactory().build()
    pipeline.add_component("catalog_retriever", Neo4jCatalogRetriever(
        graph_store=graph_store,
        scope_id=settings.OM_SCOPE_ID if settings.SCOPE_FILTER_ENABLED else None,
        max_seeds=top_k,
        max_tables=settings.EXPLORE_MAX_TABLES,
        max_columns=settings.EXPLORE_MAX_COLUMNS,
        expand_neighbors=settings.EXPLORE_EXPAND_NEIGHBORS,
    ))

    pipeline.add_component("prompt_builder", ChatPromptBuilder(
        template=[
            ChatMessage.from_system(SYSTEM_PROMPT),
            ChatMessage.from_user(USER_PROMPT),
        ],
        required_variables=["query"],
        variables=["documents", "joins", "domains", "query"],
    ))
    pipeline.add_component("llm", OpenAIChatGeneratorFactory().build())
    pipeline.add_component("answer_builder", ExploreAnswerBuilder(
        max_questions=settings.EXPLORE_QUESTIONS_PER_TABLE,
        llm_reorder=settings.EXPLORE_LLM_REORDER,
        max_listed_tables=settings.EXPLORE_MAX_LISTED_TABLES,
    ))

    pipeline.connect("dense_text_embedder.embedding", "hybrid_retriever.query_embedding")
    pipeline.connect("sparse_text_embedder.sparse_embedding", "hybrid_retriever.query_sparse_embedding")
    pipeline.connect("hybrid_retriever.documents", "catalog_retriever.documents")
    pipeline.connect("catalog_retriever.documents", "prompt_builder.documents")
    pipeline.connect("catalog_retriever.joins", "prompt_builder.joins")
    pipeline.connect("catalog_retriever.domains", "prompt_builder.domains")
    pipeline.connect("prompt_builder.prompt", "llm.messages")
    pipeline.connect("llm.replies", "answer_builder.replies")
    pipeline.connect("catalog_retriever.documents", "answer_builder.documents")
    pipeline.connect("catalog_retriever.joins", "answer_builder.joins")
    pipeline.connect("catalog_retriever.domains", "answer_builder.domains")
    pipeline.connect("catalog_retriever.catalog", "answer_builder.catalog")

    return pipeline
