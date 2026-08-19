# ADR-0015: Amostra de dados reais na resposta do modo Explorar

- **Status**: Proposto
- **Data**: 2026-08-19
- **Autores**: Davi (graphrag-tais)
- **Revisores**: -

## Introdução ao problema

Até aqui o assistente só devolveu **metadados**. O Qdrant guarda embeddings de
cards de schema, o Neo4j guarda o grafo de tabelas e colunas, e as linhas moram
num PostgreSQL analítico à parte. A Persona 2 executa SQL, mas transforma o
resultado em **gráfico** — a pessoa vê a agregação, nunca o dado. A Persona 1
([ADR-0014](./0014-personas-e-modos-de-uso-do-assistente.md)) sugere um
`sample_sql` "para ver o dado" e **não o executa**: quem está explorando recebe
uma consulta para levar a outro lugar.

Isso deixa sem resposta justamente a pergunta que a Persona 1 mais faz e que
descrição de catálogo nenhuma responde: *"o que tem dentro dessa coluna?"*. Um
`grain` declarado e uma descrição não dizem se `uf` vem como `SP` ou
`São Paulo`, se a data é `date` ou texto, se a coluna está preenchida. Dez
linhas dizem.

A decisão precisa ser tomada agora porque a mudança **não é de interface, é de
postura**: passa a existir dado real na resposta e num object store. Isso levanta
questões de escopo (o que pode ser executado), de custo (execução por pergunta) e
de exposição (o arquivo baixável) que não existiam enquanto tudo era metadado.

Restrições herdadas: a execução read-only já existe via postgres-mcp
([ADR-0013](./0013-execucao-via-postgres-mcp-e-grafico-chart-spec-no-v1.md)); o
MinIO com URL assinada já existe para o relatório
([ADR-0010](./0010-camada-de-entrega-tabelas-grafico-explicacao-relatorio.md)); o
modo Explorar é território **estritamente aditivo** e a Persona 2 não pode ser
tocada (ADR-0014); e o `sample_sql` é gerado por LLM, portanto **não confiável
por construção**.

## Decisão

**O modo Explorar executa o `sample_sql`, mostra as primeiras linhas na resposta
e publica a amostra completa como CSV para download.** A execução é governada por
uma escada determinística, e a garantia de que sempre há amostra **não vem do
LLM**:

| # | degrau | LLM | o que resolve |
|--:|---|:--:|---|
| 1 | **guarda** (`sample_sql.guard`): uma instrução, leitura só, envelopada em `SELECT * FROM (…) LIMIT n` | não | consulta perigosa e `LIMIT` ausente |
| 2 | **execução direta** pelo postgres-mcp | não | o caso normal |
| 3 | **autocorreção** pelo `Agent`, com contexto enxuto (as colunas da tabela alvo) e poucos passos | sim | preserva a **intenção** da consulta quando o banco recusa |
| 4 | **reconstrução pelo catálogo** (`SELECT <colunas do Neo4j> FROM <tabela alvo> LIMIT n`) | não | **garante** amostra; perde a intenção |

Três regras fecham a decisão:

1. **A garantia é determinística, a inteligência é opcional.** O degrau 4 usa
   colunas que vieram do catálogo, então roda salvo se o catálogo estiver
   desatualizado — e nesse caso a falha **é informação sobre o catálogo**, e vai
   para o log com o nome da tabela. O degrau 3 (LLM) existe para não rebaixar uma
   contagem por UF a linhas cruas, e é desligável por env sem tirar a amostra.
2. **Read-only não é confiado a uma camada só.** O postgres-mcp roda em modo
   RESTRICTED, e mesmo assim a guarda recusa antes de enviar. O SQL vem de um
   LLM: duas barreiras baratas valem mais que uma confiança.
3. **O que a pessoa vê é o que rodou.** A resposta mostra a consulta que
   produziu aquelas linhas — não a proposta original quando ela foi corrigida ou
   substituída. É a mesma invariante já aplicada à Persona 2.

### Entrega

- **Prévia no chat**: até 10 linhas e 6 colunas (colunas com valor primeiro),
  nulo exibido como `—`, coluna numérica alinhada à direita. É recorte de
  leitura, não de trabalho.
- **Arquivo**: CSV com 100 linhas, `utf-8-sig` + `;` — o par que faz o Excel em
  português abrir com dois cliques, acento certo e colunas separadas. Foi
  escolhido sobre `.xlsx` porque o artefato é **dado** (o destino é pandas,
  Power BI ou Excel), diferente do relatório `.docx`, que é **documento**.
- **Armazenamento**: bucket `amostras`, separado do bucket de relatórios, com
  URL assinada de validade curta (15 min contra 7 dias). Linhas do banco não
  merecem a mesma exposição que um relatório que a pessoa pediu.

### Custo

O caminho normal **não gasta LLM nenhum** — a execução direta já existia e
dispensa o `Agent`. O custo por resposta é um `SELECT ... LIMIT 100` no banco.
Só o degrau 3 gasta modelo, só quando o banco recusa, e com prompt de dezenas de
tokens em vez do catálogo inteiro (25 cards) que o `schema_context` da Persona 2
carregaria.

### Medição

A execução transforma `sample_sql_grounded` (o SQL cita colunas que existem) em
**`sample_sql_executable`** (o SQL roda), sinal bem mais forte e disponível sem
gold novo: `run_api` passa a devolver `sample`, com `origin` ∈
{`llm`, `corrigido`, `catalogo`}. A distribuição de `origin` sobre os casos do
gold é a métrica que diz se o degrau 3 se paga.

## Alternativas consideradas

### Alternativa A: amostra determinística, sem usar o `sample_sql` do LLM

- `SELECT <colunas> FROM <tabela top-1> LIMIT 10`, sempre.
- **Prós**: zero risco de alucinação, zero LLM, uma linha de código.
- **Contras**: joga fora o que o `sample_sql` tem de melhor — ele escolhe
  colunas pertinentes e às vezes uma contagem por categoria, que informa mais que
  dez linhas cruas. **Rejeitada como caminho principal**, adotada como o degrau
  4: é a rede de segurança, não a proposta.

### Alternativa B: entregar `.xlsx` em vez de CSV

- **Prós**: sem ambiguidade de separador/encoding; tipos preservados.
- **Contras**: dependência nova (`openpyxl`) e formato pior para o destino real
  do dado (script/BI). **Rejeitada** por ora; vira knob se o público for
  exclusivamente Excel.

### Alternativa C: amostra também na Persona 2

- Ela já executa o SQL e **já tem as linhas** na mão para o gráfico; hoje as
  descarta.
- **Adiada, não rejeitada**: é barato (o dado já está lá) e faz sentido oferecer
  o CSV ao lado do `.docx`. Fica fora deste ADR para não misturar uma mudança na
  superfície medida da Persona 2 com a estreia do recurso.

### Não fazer nada

A Persona 1 continua recebendo uma consulta para executar em outro lugar — ou
seja, o assistente responde "o que existe" mas nunca "como isso é". É a persona
que menos tem como conferir por conta própria.

## Tradeoffs

### Vantagens

- **[Alto impacto]** Responde a pergunta que o catálogo não responde: como o
  dado realmente é.
- **[Alto impacto]** A amostra **sempre sai** (degrau 4), e sair é o que
  importa para quem está explorando.
- **[Médio impacto]** Ganha-se um sinal de qualidade forte e barato
  (`sample_sql_executable`), e um detector de **catálogo desatualizado**.
- **[Médio impacto]** A Persona 1 segue barata: nenhum LLM a mais no caminho
  normal.

### Desvantagens

- **[Alto impacto]** É o primeiro dado real fora do banco. Um CSV no MinIO é
  distribuição: mitigado por bucket separado, URL curta e teto de 100 linhas, mas
  a natureza da mudança permanece.
- **[Médio impacto]** Coluna com dado sensível entra na amostra sem filtro. A
  ingestão já guarda `tags` por coluna (`tagFQN`); excluir coluna marcada como
  PII é o gancho natural e **não está implementado** — enquanto o escopo for
  MCid (dado público), é risco aceito e declarado.
- **[Médio impacto]** A amostra é de **uma** tabela (a que o `sample_sql`
  referencia, ou a primeira do ranking), não das doze listadas. Amostrar outra é
  self-service: basta perguntar sobre ela.
- **[Baixo impacto]** Latência sobe pelo tempo de um `SELECT ... LIMIT 100`.

## Consequências

- `EXPLORE_EXECUTE_SAMPLE` passa a **`true`** por default (era `false`, "até ser
  medido"); `EXPLORE_SAMPLE_AUTOCORRECT` desliga só o degrau 3.
- `McpSqlExecutor` ganha `direct_only` (aditivo, default preserva a Persona 2) e
  `MinIOUploader` ganha bucket, content-type e validade parametrizáveis.
- O modo Explorar deixa de ser "uma chamada de LLM e nada mais": passa a
  depender do postgres-mcp e do banco analítico — **degradando sem erro** quando
  eles não respondem (a resposta volta a ser a de antes, com o SQL de amostra).
