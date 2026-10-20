# ADR-0019: Volta ao postgres-mcp, com túnel SSH até o banco

- **Status**: Proposto
- **Data**: 2026-09-09
- **Autores**: Codex, Claude (graphrag-tais)
- **Revisores**: -

> Este ADR **revisa o mecanismo de execução** decidido no
> [ADR-0017](./0017-execucao-do-sql-via-trino-mcp.md), voltando de `trino-mcp`
> para `postgres-mcp`. Preserva o contrato de backend configurável que o 0017
> criou — é justamente ele que torna esta volta uma mudança de `.env`, e não de
> código. Continuam valendo: o `Agent` com auto-correção e os guardrails
> read-only do [ADR-0013](./0013-execucao-via-postgres-mcp-e-grafico-chart-spec-no-v1.md),
> o mascaramento do [ADR-0018](./0018-mascaramento-de-dado-sensivel-na-saida.md)
> e a regra do [ADR-0004](./0004-openmetadata-como-fonte-de-metadados.md) de o
> RAG **não segurar credencial de banco** — quem a segura é o container do MCP.

## Introdução ao problema

O ADR-0017 trocou o executor por Trino porque o acesso ao dado do GovHub passou
a ser concedido por usuário do Trino. A decisão estava certa para o problema de
governança que resolvia, mas introduziu uma falha de dado que só apareceu com o
escopo MinC em produção.

**O conector `postgresql` do Trino não expõe colunas `numeric` sem precisão e
escala declaradas.** O `DECIMAL` do Trino é de precisão fixa; uma coluna
PostgreSQL `numeric` sem parâmetros tem `numeric_precision` e `numeric_scale`
NULL no `information_schema`, então o conector a classifica como tipo não
suportado e **a descarta em silêncio** — sem erro, sem aviso, sem log visível
para quem consome.

Medido no escopo MinC, comparando as colunas do OpenMetadata com o
`information_schema` do catálogo `hml_analytics`:

| | |
|---|---:|
| colunas no catálogo | 6.874 |
| **ausentes no Trino** | **390 (5,7%)** |
| tabelas ausentes | **0 de 547** |
| tabelas com pelo menos uma coluna ausente | **188 (34%)** |
| colunas ausentes que são `NUMERIC` | **390 (100%)** |
| colunas `NUMERIC` presentes | **0** |
| colunas `decimal`/`double` em QUALQUER schema do catálogo | **0** |

A assinatura é estritamente unidirecional — 390 faltando, **zero** sobrando —
o que descarta evolução de schema e isola o tipo como única variável.

O efeito prático é que **nenhuma pergunta sobre dinheiro tinha resposta**:
`valor`, `valor_aprovado`, `vlcaptado`, `valor_captado`, `valor_total`,
`valor_investido_total` — todas invisíveis. E o modo de falha era o pior
possível: o catálogo afirma que a coluna existe, o LLM escreve SQL correto, o
banco responde `Column cannot be resolved`, a auto-correção esgota as tentativas
e a resposta sai como "SQL gerado" sem gráfico. Para quem lê, parece erro do
modelo.

A causa raiz está numa macro do dbt (`bronze_numerico`, usada por 189 modelos)
que emite `::numeric` sem precisão. Consertar lá é o certo a longo prazo, mas
são 390 colunas em 188 tabelas, e não é trabalho deste repositório.

## Decisão

Voltar a executar o SQL no **PostgreSQL**, via `postgres-mcp`, alcançando o
banco por um **túnel SSH** montado por um container próprio.

O que muda:

- **`postgres-tunnel`** (`Dockerfile.postgres-tunnel`, alpine + openssh-client)
  abre `ssh -N -L 0.0.0.0:${TUNNEL_LOCAL_PORT}:${ANALYTICS_DB_HOST}:${ANALYTICS_DB_PORT}`
  contra o bastion. A chave privada entra por **bind read-only** em
  `/run/secrets/tunnel_key`: o caminho no host é do desenvolvedor
  (`PRIVATE_KEY_PATH`), o caminho dentro do container é fixo, então o compose
  não depende de onde cada um guarda a chave.
- **`postgres-mcp`** recebe `DATABASE_URI` apontando para `postgres-tunnel`, e
  declara `depends_on: condition: service_healthy`. O healthcheck do túnel
  (`nc -z` na porta encaminhada) é o que garante a ordem — sem ele o MCP sobe
  antes de haver túnel e falha na primeira consulta.
- **`trino-mcp`** passa para o profile `trino`. Não some: continua disponível
  para comparação explícita (`docker compose --profile trino up -d trino-mcp`).
- `SQL_MCP_*` e `SQL_DIALECT` voltam para o par Postgres. **Nenhuma linha de
  código de execução mudou** — é o contrato configurável do ADR-0017 fazendo
  seu trabalho.

## Consequências

**A favor:**

- As 390 colunas numéricas voltam a existir. Validado: `SUM(valor)` em
  `agentes.eventos_fomento` responde (ROUANET R$ 35,5 bi), e a pergunta que não
  gerava gráfico passou a gerar (PNG de 27 KB, com `### SQL executado`).
- O dialeto volta a ser PostgreSQL, que é o que o `sqlglot` e o `eval` do
  projeto sempre trataram melhor, e o que o LLM erra menos.
- Some a dependência do certificado self-signed do Trino e do par
  `SSL_CERT_FILE` + `extra_hosts` que o ADR-0017 precisou montar.

**Contra, e é preciso assumir:**

- **Volta a exigir credencial de banco**, que foi exatamente o motivo do
  ADR-0017. A restrição de governança que motivou a troca continua de pé; esta
  decisão a contorna com um usuário de leitura alcançável por bastion, e isso
  precisa ser aceito por quem cuida do acesso.
- **Nova peça de infraestrutura no caminho crítico.** O túnel é um ponto de
  falha a mais: se o bastion cair, o RAG perde execução de SQL inteira. O
  healthcheck detecta, o `restart: unless-stopped` reergue, mas a dependência é
  real.
- **A chave privada passa a existir dentro de um container.** É bind read-only e
  não entra na imagem, mas quem tiver exec no container a lê.

## Alternativas consideradas

- **`decimal-mapping=allow_overflow` no catálogo do Trino** — uma linha de
  configuração resolveria. Descartada por não termos acesso ao Trino, e porque
  força uma escala padrão única para colunas de perfis diferentes (monetário,
  índice, percentual).
- **Declarar `numeric(p,s)` nos modelos dbt** — é a correção na origem, e a
  melhor tecnicamente: cada coluna com a escala que lhe cabe, e vale para
  qualquer ferramenta que leia o banco. Descartada **para agora** por alcance
  (390 colunas, 189 modelos) e por ser trabalho de outro repositório. Continua
  recomendada de forma independente desta decisão.
- **Esconder as colunas fantasma do catálogo** (marcar no Neo4j e filtrar na
  serialização). Faria o sistema falhar com dignidade, mas não devolveria a
  capacidade: perguntas sobre dinheiro seguiriam sem resposta. Descartada por
  tratar o sintoma.
- **Passagem direta pelo conector** (`TABLE(hml_analytics.system.query(...))`)
  para castar do lado do Postgres. Testada e **negada por permissão**:
  `Access Denied: Cannot execute function hml_analytics.system.query`.

## Pendências

- O gate de integridade da ingestão — comparar cada coluna do catálogo contra o
  `information_schema` do alvo de execução — continua valendo, e teria pego esta
  classe de erro no primeiro dia em vez de em produção. Não depende de qual
  backend está ativo.
- A VM de homologação (`10.0.0.210`) ainda roda a configuração do ADR-0017 e
  precisa da mesma migração.
