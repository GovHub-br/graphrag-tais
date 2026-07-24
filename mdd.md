Sim, o **RAGAS entra parcialmente**, mas ele não é suficiente para avaliar GraphRAG. Ele consegue medir se os conteúdos ou entidades relevantes foram recuperados, porém, por padrão, não entende:

* quais arestas foram percorridas;
* se o caminho recuperado está correto;
* se os nós formam um subgrafo conectado;
* se a expansão adicionou contexto útil ou apenas ruído;
* se foi encontrado um caminho de join válido entre tabelas.

Essa ausência de um protocolo universal ainda é um problema da área: estudos recentes apontam que sistemas GraphRAG usam avaliações bastante heterogêneas, enquanto benchmarks mais novos tentam separar construção do grafo, retrieval e geração. ([arXiv][1])

## 1. O que exatamente você precisa avaliar

No seu caso, eu dividiria o retrieval em:

```text
Pergunta
   ↓
Recuperação semântica inicial
   ↓
Nós-semente
   ↓
Expansão/travessia no Neo4j
   ↓
Subgrafo recuperado
   ↓
Contexto enviado ao SQL Builder
```

A avaliação deve responder separadamente:

1. Os nós-semente corretos foram encontrados?
2. A expansão encontrou os nós que estavam faltando?
3. As relações e caminhos corretos foram recuperados?
4. A expansão trouxe ruído?
5. O subgrafo final é suficiente para construir o SQL?
6. Quanto custou chegar nesse subgrafo?

Isso se aproxima do fluxo empregado pelo KG²RAG: primeiro são recuperados elementos-semente semanticamente e, depois, o grafo é usado para expandir e organizar o contexto. ([ACL Anthology][2])

---

# 2. Você precisa de um gold standard em forma de subgrafo

Para cada pergunta de teste, você deveria armazenar algo parecido com:

```yaml
question: "Quantas mulheres negras recebem bolsas no IPEA?"

gold_nodes:
  tables:
    - fato_bolsas
    - dim_pessoa
  columns:
    - fato_bolsas.pessoa_id
    - dim_pessoa.id
    - dim_pessoa.sexo
    - dim_pessoa.raca
  metrics:
    - quantidade_beneficiarios
  glossary:
    - mulher
    - pessoa_negra

gold_edges:
  - fato_bolsas.pessoa_id -> dim_pessoa.id
  - dim_pessoa.sexo -> glossario.mulher
  - dim_pessoa.raca -> glossario.pessoa_negra

gold_paths:
  - fato_bolsas -> pessoa_id -> dim_pessoa
```

Esse gold pode ser construído a partir de:

* SQL validado por especialista;
* tabelas e colunas referenciadas no SQL;
* joins necessários;
* métricas utilizadas;
* filtros semânticos;
* caminhos alternativos considerados válidos.

O ideal não é anotar “tudo que pode ter alguma relação”. É anotar o **subgrafo mínimo suficiente** para responder à pergunta.

---

# 3. Avaliação dos nós-semente

Essa etapa avalia principalmente Qdrant, embeddings e reranking, antes da expansão.

## Recall@K de nós

Mede quantos nós relevantes apareceram nos primeiros `K` resultados:

[
NodeRecall@K =
\frac{|V_{seed@K} \cap V_{gold}|}
{|V_{gold}|}
]

Exemplo:

```text
Gold:     {fato_bolsas, dim_pessoa, sexo, raça}
Top-5:    {fato_bolsas, dim_servidor, dim_pessoa, órgão, sexo}

Recall@5 = 3/4 = 0,75
```

## Precision@K

Mede quanto dos resultados iniciais é realmente relevante:

[
NodePrecision@K =
\frac{|V_{seed@K} \cap V_{gold}|}
{|V_{seed@K}|}
]

Recall é mais importante nessa etapa quando a expansão consegue remover ou compensar ruído. Porém, uma precisão muito baixa faz o grafo expandir a partir de nós errados.

## Hit@K

Verifica se pelo menos um nó relevante apareceu entre os `K` primeiros:

[
Hit@K =
\mathbb{1}(V_{seed@K} \cap V_{gold} \neq \varnothing)
]

É útil, mas fraco sozinho. Encontrar apenas `dim_pessoa`, por exemplo, pode dar `Hit@5 = 1`, embora o retrieval tenha perdido todo o restante.

## MRR

Avalia em que posição aparece o primeiro nó relevante:

[
MRR =
\frac{1}{N}\sum_q\frac{1}{rank_q}
]

É bom para verificar se o ponto de partida da travessia aparece cedo.

## nDCG@K

É adequado quando você possui graus diferentes de relevância:

```text
3 = essencial
2 = importante
1 = relacionado
0 = irrelevante
```

Por exemplo, para a pergunta sobre mulheres negras e bolsas:

```text
fato_bolsas        → 3
dim_pessoa         → 3
coluna_raca        → 3
dim_programa       → 1
dim_organizacao    → 0 ou 1
```

Na literatura de retrieval em grafos, métricas como Hit@1, recall e F1 continuam sendo utilizadas para recuperação de entidades. 

---

# 4. Avaliação específica da expansão no grafo

Aqui estão as métricas mais importantes para o seu caso.

## 4.1 Expansion Recall Gain

Mede quanto a expansão acrescentou de cobertura:

[
ExpansionGain =
Recall(V_{expanded}) - Recall(V_{seed})
]

Exemplo:

```text
Recall dos seeds:       0,40
Recall após expansão:   0,90
Expansion Gain:         +0,50
```

Essa é provavelmente a métrica mais clara para provar que o Neo4j está agregando valor além do Qdrant.

Mas ela precisa ser analisada junto com a precisão: sair de 2 para 200 nós pode aumentar o recall sem representar uma boa expansão.

---

## 4.2 Marginal Expansion Precision

Avalia somente os novos nós adicionados pela travessia:

[
ExpansionPrecision =
\frac{|(V_{expanded} - V_{seed}) \cap V_{gold}|}
{|V_{expanded} - V_{seed}|}
]

Exemplo:

```text
A expansão adicionou 20 nós.
5 deles pertencem ao gold.

Expansion Precision = 5/20 = 0,25
```

Ela responde diretamente:

> Dos nós adicionados pelo grafo, quantos realmente eram úteis?

---

## 4.3 Node Precision, Recall e F1 do subgrafo

Considerando:

* (V^*): nós do gold;
* (\hat{V}): nós recuperados.

[
NodePrecision =
\frac{|\hat{V} \cap V^*|}{|\hat{V}|}
]

[
NodeRecall =
\frac{|\hat{V} \cap V^*|}{|V^*|}
]

[
NodeF1 =
2\frac{Precision \cdot Recall}{Precision + Recall}
]

A diferença em relação ao `Recall@K` é que, aqui, você avalia o subgrafo final completo, depois da travessia e da poda.

---

## 4.4 Edge Precision, Recall e F1

Recuperar os nós certos não significa recuperar as relações certas.

[
EdgeRecall =
\frac{|\hat{E} \cap E^*|}{|E^*|}
]

[
EdgePrecision =
\frac{|\hat{E} \cap E^*|}{|\hat{E}|}
]

Isso é essencial no seu GraphRAG porque:

```text
fato_bolsas → dim_pessoa
```

é semanticamente diferente de:

```text
fato_bolsas → dim_programa → dim_pessoa
```

Também vale considerar no matching:

* tipo da relação;
* direção;
* coluna de origem;
* coluna de destino;
* cardinalidade;
* confiança;
* validade temporal, quando houver.

---

## 4.5 Path Recall

Em perguntas multi-hop, não basta recuperar os nós isoladamente. Você precisa recuperar o caminho completo.

[
PathRecall =
\frac{\text{número de caminhos gold integralmente recuperados}}
{\text{número de caminhos gold}}
]

Exemplo:

```text
Gold:
fato_bolsas
  → pessoa_id
  → dim_pessoa
  → raça
  → conceito_pessoa_negra
```

O sistema recuperou todos os nós, mas não recuperou a relação entre `raça` e `conceito_pessoa_negra`.

Nesse caso:

```text
Node Recall = 1,0
Path Recall = 0,0
```

Essa diferença demonstra por que métricas tradicionais de RAG não bastam.

---

## 4.6 Structural Sufficiency Rate

Essa seria uma das minhas métricas principais para o seu TCC.

Para cada pergunta:

```text
1 → o subgrafo contém pelo menos um caminho válido
    que conecta todos os elementos necessários;

0 → não contém.
```

Depois:

[
StructuralSufficiencyRate =
\frac{\text{consultas com subgrafo estruturalmente suficiente}}
{\text{total de consultas}}
]

No seu domínio, “estruturalmente suficiente” poderia significar:

* todas as tabelas obrigatórias estão presentes;
* existe um caminho de join válido entre elas;
* as colunas de filtro estão presentes;
* a métrica ou medida necessária está presente;
* as relações semânticas necessárias estão presentes.

Isso avalia o retrieval sem depender da qualidade do LLM que gera o SQL.

---

## 4.7 Join Path Recall

Uma versão específica para Text-to-SQL:

[
JoinPathRecall =
\frac{\text{caminhos de join válidos recuperados}}
{\text{caminhos de join gold}}
]

Você pode calcular em dois níveis:

```text
Exact Join Path Recall
O caminho recuperado é exatamente igual ao gold.

Valid Join Path Recall
O caminho é diferente, mas semanticamente e relacionalmente válido.
```

Isso é importante porque, em bancos reais, podem existir vários caminhos válidos entre tabelas.

---

## 4.8 Hop Accuracy

Avalia se a profundidade da expansão foi adequada.

```text
Gold necessita de 2 hops.
Sistema percorreu 2 hops  → correto
Sistema percorreu 1 hop   → insuficiente
Sistema percorreu 5 hops  → possível excesso de expansão
```

Uma forma simples:

[
HopError =
|h_{retrieved} - h_{gold}|
]

Mas eu não trataria todo hop adicional como erro automaticamente. O importante é avaliar o aumento de ruído e custo.

Estudos com recuperação de ego-graphs mostram que expandir mais ou recuperar mais subgrafos não melhora indefinidamente o resultado; depois de determinado ponto, nós irrelevantes podem prejudicar o sistema. 

---

## 4.9 Seed-to-Gold Distance

Serve para diagnosticar a recuperação inicial.

Para cada nó relevante não recuperado como seed, calcule a menor distância até algum seed:

[
SeedDistance =
\frac{1}{|V^*|}
\sum_{v \in V^*}
\min_{s \in V_{seed}}d(s,v)
]

Interpretação:

```text
Distância média próxima de 1:
os seeds estão próximos do subgrafo correto;
a expansão provavelmente consegue corrigir.

Distância média alta:
o retrieval semântico começou em uma região errada do grafo.
```

Isso ajuda a separar:

```text
falha de retrieval vetorial
versus
falha da estratégia de travessia
```

---

# 5. Métricas de excesso e ruído

## Overexpansion Ratio

[
Overexpansion =
\frac{|\hat{V} - V^*|}
{|V^*|}
]

Exemplo:

```text
Gold: 8 nós
Subgrafo recuperado: 40 nós
Nós incorretos: 32

Overexpansion = 32/8 = 4
```

Ou seja, foram adicionados quatro nós irrelevantes para cada nó esperado no gold.

## Subgraph Size Ratio

[
SubgraphSizeRatio =
\frac{|\hat{V}|}{|V^*|}
]

Idealmente, deve ficar próximo de `1`, embora não necessariamente exatamente em `1`.

## Redundancy Rate

Mede quantos nós recuperados representam informações duplicadas ou equivalentes:

```text
Tabela duplicada de outra fonte
Sinônimos repetidos
Colunas semanticamente equivalentes
Múltiplos chunks do mesmo metadado
```

## Disconnected Node Rate

[
DisconnectedRate =
\frac{\text{nós sem conexão útil com o componente principal}}
{|\hat{V}|}
]

Um GraphRAG que retorna muitos nós individualmente relevantes, mas desconectados, pode ter boa precisão textual e ainda assim formar um contexto estruturalmente ruim.

---

# 6. Métricas específicas para o seu GraphRAG de SQL

Eu criaria uma métrica chamada **SQL Dependency Recall**.

A partir do SQL gold, extraia:

```text
Tabelas
Colunas selecionadas
Colunas de filtro
Colunas de agregação
Joins
Métricas
Dimensões
Conceitos do glossário
Granularidade
```

Depois:

[
SQLDependencyRecall =
\frac{\text{dependências do SQL gold presentes no subgrafo}}
{\text{total de dependências do SQL gold}}
]

Também pode separar:

```text
Table Recall
Column Recall
Filter Column Recall
Join Recall
Metric Recall
Dimension Recall
Glossary Recall
Grain Recall
```

Eu usaria pesos diferentes:

```text
Join correto:              peso 3
Tabela obrigatória:        peso 3
Coluna de filtro:          peso 3
Métrica:                   peso 3
Granularidade:             peso 2
Descrição auxiliar:        peso 1
Linhagem complementar:     peso 1
```

Então você teria:

[
WeightedDependencyRecall =
\frac{\sum_i w_i \cdot recovered_i}
{\sum_i w_i}
]

Isso evita que dez descrições secundárias compensem a ausência de um join obrigatório.

---

# 7. Onde o RAGAS entra

O RAGAS possui métricas úteis para retrieval:

* `ContextPrecision`;
* `ContextRecall`;
* `ContextEntityRecall`;
* `IDBasedContextPrecision`;
* `IDBasedContextRecall`.

O `Context Precision` avalia se contextos relevantes aparecem antes de contextos irrelevantes. O `Context Recall` verifica quanto da informação de referência foi recuperado. As variantes baseadas em ID podem comparar diretamente IDs recuperados e esperados, sem precisar usar um LLM como juiz. ([Ragas][3])

O `ContextEntityRecall` também pode ajudar a medir quantas entidades do gold aparecem no contexto recuperado. ([Ragas][4])

Para seu sistema, você poderia mapear:

```text
retrieved_context_ids:
  - table:fato_bolsas
  - table:dim_pessoa
  - column:dim_pessoa.raca
  - metric:quantidade_beneficiarios

reference_context_ids:
  - table:fato_bolsas
  - table:dim_pessoa
  - column:dim_pessoa.raca
  - column:dim_pessoa.sexo
  - metric:quantidade_beneficiarios
```

Assim, o RAGAS calcula precision e recall de nós.

Porém, ele enxergaria isso apenas como uma lista. Ele não saberia distinguir:

```text
A → B → C
```

de:

```text
A    B    C
```

Portanto:

> **Use o RAGAS para métricas de conteúdo e entidades, mas implemente métricas próprias para arestas, caminhos, conectividade, expansão e suficiência estrutural.**

O RAGAS permite criar métricas customizadas, então ele ainda pode ser usado como framework de execução e armazenamento das avaliações, mesmo que os cálculos topológicos sejam seus. ([Ragas][5])

---

# 8. Conjunto mínimo que eu recomendaria

Para não transformar sua bancada em algo gigantesco, eu usaria estas métricas principais:

| Etapa      | Métrica                        | O que demonstra                                 |
| ---------- | ------------------------------ | ----------------------------------------------- |
| Seeds      | Node Recall@K                  | Se o vetor encontrou bons pontos de entrada     |
| Seeds      | MRR ou nDCG@K                  | Se os melhores seeds ficaram no topo            |
| Expansão   | Expansion Recall Gain          | Quanto o grafo melhorou a cobertura             |
| Expansão   | Marginal Expansion Precision   | Quanto da expansão foi realmente útil           |
| Subgrafo   | Node F1                        | Qualidade dos nós recuperados                   |
| Subgrafo   | Edge Recall/F1                 | Qualidade das relações                          |
| Subgrafo   | Path Recall                    | Se os caminhos multi-hop foram recuperados      |
| SQL        | Structural Sufficiency Rate    | Se o contexto é suficiente para montar o SQL    |
| SQL        | Weighted SQL Dependency Recall | Cobertura de tabelas, joins, filtros e métricas |
| Eficiência | Overexpansion Ratio            | Quantidade de ruído introduzida                 |
| Eficiência | Latência P50/P95               | Custo operacional da travessia                  |

Minha métrica central seria uma combinação parecida com:

```text
Graph Retrieval Quality =
  alta cobertura das dependências
+ caminhos válidos
+ pouco excesso de expansão
```

Eu evitaria criar uma média única logo de início. É melhor apresentar pelo menos três eixos:

```text
Cobertura estrutural
Precisão/minimalidade
Custo
```

---

# 9. Experimento que faria mais sentido

Compare:

```text
A. Vetorial puro
B. Vetorial + seeds, sem expansão
C. Vetorial + expansão de 1 hop
D. Vetorial + expansão de 2 hops
E. Vetorial + expansão de 2 hops + pruning
F. GraphRAG com sua estratégia completa
G. Oracle: subgrafo gold
```

E registre para cada configuração:

```text
Node Recall
Edge Recall
Path Recall
Structural Sufficiency
Número de nós
Número de arestas
Latência
```

Isso permite demonstrar algo muito mais forte que “GraphRAG teve score X”:

> A expansão de dois hops aumentou a cobertura das dependências SQL, mas também elevou o ruído; a aplicação de pruning preservou os caminhos necessários e reduziu o tamanho médio do subgrafo.

Essa avaliação por ablação também é coerente com experimentos recentes de GraphRAG, que comparam profundidade, quantidade de entidades recuperadas e presença ou ausência de mecanismos de pruning. 

Em resumo: **RAGAS cobre a superfície do retrieval; suas métricas de grafo devem avaliar a estrutura recuperada. Para o seu projeto, o objeto avaliado deve ser o subgrafo necessário para construir o SQL, não apenas os textos enviados ao modelo.**

[1]: https://arxiv.org/abs/2506.02404 "[2506.02404] GraphRAG-Bench: Challenging Domain-Specific Reasoning for Evaluating Graph Retrieval-Augmented Generation"
[2]: https://aclanthology.org/2025.naacl-long.449/ "Knowledge Graph-Guided Retrieval Augmented Generation - ACL Anthology"
[3]: https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/ "Context Precision - Ragas"
[4]: https://docs.ragas.io/en/v0.4.0/concepts/metrics/available_metrics/context_entities_recall/?utm_source=chatgpt.com "Context Entities Recall - Ragas"
[5]: https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/?utm_source=chatgpt.com "List of available metrics - Ragas"
