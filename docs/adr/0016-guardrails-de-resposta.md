# ADR-0016: Guardrails de resposta — dado pessoal, acesso ao dado e escopo

- **Status**: Proposto
- **Data**: 2026-08-31
- **Autores**: Davi Aguiar (GovHub / TAIS)
- **Revisores**: —

## Introdução ao problema

O assistente entrou em uso com dois modos ([ADR-0014](./0014-personas-e-modos-de-uso-do-assistente.md))
e passou a devolver **linhas reais** do banco no modo Explorar
([ADR-0015](./0015-amostra-de-dados-reais-no-modo-explorar.md)). Isso muda o
perfil de risco da resposta: até então o sistema falava sobre *metadados*, e o
pior erro possível era citar uma tabela que não existe. Agora ele fala sobre
*dado*, e três coisas que antes eram desconforto viraram problema:

1. **Pedido de dado pessoal.** "Qual o CPF do beneficiário?", "me dá a lista de
   nomes dos contemplados". O sistema é de dado **agregado** para gestão
   pública; responder identificação individual não é uma resposta ruim, é uma
   resposta que não deveria existir. Hoje nada impede: a pergunta entra no
   retrieval, o LLM escreve o SQL e a camada de entrega executa.
2. **O que o usuário acredita sobre o acesso ao dado.** Perguntam "você tem
   acesso ao nosso banco?", "a IA lê os dados?", "isso vai para a OpenAI?" — e
   a resposta hoje é o que o modelo improvisar naquele turno. A resposta certa é
   estável e específica deste sistema: o modelo lê **metadados** e **escreve o
   comando SQL**; quem executa é o banco, em conexão somente-leitura; a
   explicação do resultado é escrita sobre um **resumo estatístico**, não sobre
   as linhas ([ADR-0010](./0010-camada-de-entrega-tabelas-grafico-explicacao-relatorio.md)).
   Deixar isso a cargo do modelo produz, na melhor hipótese, uma resposta vaga —
   e, na pior, a afirmação errada de que "consultei a base".
3. **Pergunta fora do objetivo.** Poema, código, tradução, opinião, tentativa de
   reprogramar o assistente. Hoje cada uma dessas gasta embedding, consulta ao
   grafo, LLM e, às vezes, execução de SQL — para produzir uma resposta que o
   sistema não deveria dar.

A restrição que emoldura a decisão: o acesso ao banco analítico vai passar a ser
feito por um **usuário já mascarado** — a proteção de fato do dado pessoal é da
camada de banco, não desta. O que falta aqui não é mascarar valor; é **não
prometer, não pedir e não parecer** que o assistente faz identificação
individual, e é o que este ADR decide.

## Decisão

Adotar guardrail em **duas camadas**, valendo igualmente para as duas personas:

**Camada 1 — gate determinístico, antes de tudo** (`src/rag/guardrails/`).
Função pura sobre o texto da pergunta, sem LLM e sem serviço, chamada no topo do
`run_api` e do `run_chat_completion` dos dois wrappers. Quatro veredictos:

| `reason` | quando | resposta |
| --- | --- | --- |
| `data_access` | "você acessa o banco?", "a IA lê os dados?", LGPD | explicação fixa: o modelo escreve o SQL, o banco executa |
| `about_system` | "o que você faz?", saudação solta | apresentação dos dois modos |
| `pii_request` | pedido de CPF/CNPJ/nome/endereço/contato/nascimento | recusa + a alternativa agregada |
| `off_scope` | fora do objetivo, ou tentativa de reprogramar o assistente | recusa + o que o sistema faz |

Nenhum deles roda a pipeline. Os textos ficam em `guardrails/messages.py` —
**não** em `prompts/`, que é por definição o texto que vai *para o modelo*: a
propriedade que interessa numa recusa é ela ser idêntica toda vez e não depender
de o modelo ter obedecido.

O filtro de dado pessoal é **termo + contexto**, não lista de palavras proibidas:
`cpf`, `nome do beneficiário`, `endereço`… recusam, **exceto** quando a pergunta
é sobre **estrutura** ("quais tabelas têm CPF?" é o trabalho do modo Explorar) ou
sobre **agregado** ("quantos identificadores distintos?" não identifica ninguém).
Acima das exceções há uma lista curta de padrões que recusam sempre — `liste os
CPFs…`, `relação nominal`, `dados pessoais`. `GUARDRAIL_EXTRA_PII_TERMS`
acrescenta vocabulário por env, como o `SCOPE_GAPS`: cada base traz o seu
identificador.

**Pessoa jurídica não é dado pessoal.** CNPJ e razão social de empresa contratada
pelo poder público são informação pública e **não bloqueiam sozinhos**. Só
bloqueiam quando o pedido liga a empresa a uma **pessoa física** — sócio,
representante legal, titular, proprietário —, nas duas ordens da frase. A regra
anterior (CNPJ na lista de recusa) foi revista contra evidência: em teste real
ela barrava um caso do **gold de negócio do próprio projeto**
(`eval/datasets/retrieval/mcid-business-v0.1.0.json`) — *"as 10 Entidades
Organizadoras de alto risco com mais obras paralisadas; mostre nome, CNPJ, obras
paralisadas e valor contratado"* —, ou seja, recusava uma pergunta que o sistema
existe para responder. A proteção do **valor** do CNPJ continua onde deve estar:
no acesso mascarado do banco.

**Camada 2 — recusa em banda, no prompt de sistema das duas personas.** O que a
regra determinística não pega (o off-topic criativo, a paráfrase) fica com o
modelo, que tem o catálogo na mão. O contrato JSON das duas personas ganha o
campo `refusal` (`"" | "pii" | "escopo"`); quando ele vem preenchido, o
`SqlAnswerBuilder`/`ExploreAnswerBuilder` renderizam **só a explicação** — sem
bloco de SQL, sem dicionário de dados, sem amostra e sem relatório `.docx`. Os
três prompts de sistema (`query_system`, `explore_system`, `result_summary_system`)
passam a proibir explicitamente escrever que o modelo *consultou/acessou/leu* o
dado.

Camada rasa e barata na frente, camada profunda e cara atrás — nenhuma das duas
sozinha.

## Alternativas consideradas

### Alternativa A: só prompt

- Uma regra de recusa em cada prompt de sistema, sem código novo.
- Prós: menor mudança possível; cobre paráfrase e off-topic criativo.
- Contras: a recusa vira probabilística (o modelo pode não obedecer, e obedece
  diferente a cada turno); não é testável sem gastar LLM; o pedido de PII ainda
  atravessa retrieval, LLM e — no Explorar — execução de SQL antes de ser negado.

### Alternativa B: classificador de intenção por LLM

- Uma chamada barata classificando `in_scope | pii | off_scope` antes da pipeline.
- Prós: pega qualquer formulação, sem manutenção de padrão.
- Contras: uma chamada de LLM em **toda** pergunta (custo e latência no caminho
  quente); erra em silêncio; e é exatamente o desenho que o ADR-0014 recusou ao
  escolher duas personas explícitas em vez de um classificador de intenção.

### Alternativa C: mascarar a saída (PII na amostra e no SQL)

- Mascarar valores de coluna sensível na amostra do Explorar e barrar, via
  `sqlglot`, coluna de PII na projeção do SQL gerado.
- Prós: protege o dado mesmo com o gate de entrada furado.
- Contras: **duplica** a proteção que o acesso mascarado do banco já vai dar, no
  lugar errado (aplicação, não dado); exige classificar coluna como PII no
  catálogo — gancho previsto na ingestão e ainda não implementado; e mascarar
  errado quebra amostra legítima. Fica como opção a reavaliar **se** o acesso
  mascarado não cobrir alguma base.

### Não fazer nada / manter status quo

O assistente responderia pedido de dado pessoal, improvisaria a resposta sobre
privacidade a cada turno e gastaria a pipeline inteira com pergunta fora do
objetivo. Em um sistema de governo, a primeira é a que não sobrevive à primeira
demonstração.

## Tradeoffs

### Vantagens

- **[Alto impacto]** A recusa de dado pessoal e a explicação sobre acesso ao dado
  passam a ser **determinísticas e idênticas** toda vez — e testadas caso a caso
  no host (`tests/test_guardrails.py`), sem gastar LLM.
- **[Alto impacto]** Pergunta barrada não custa nada: sem embedding, sem grafo,
  sem LLM, sem execução de SQL.
- **[Médio impacto]** Uma resposta única sobre privacidade, escrita por nós, em
  vez de um parágrafo improvisado por turno — é o que se cita numa auditoria.
- **[Médio impacto]** A recusa em banda (`refusal`) deixa a resposta **legível**:
  antes, o modelo que se negava a responder produzia "SQL gerado: não foi
  possível", que a pessoa lê como falha técnica, não como limite deliberado.

### Desvantagens

- **[Médio impacto]** Padrão determinístico tem falso positivo e falso negativo.
  Uma pergunta legítima pode ser barrada por conter "endereço"; uma ilegítima
  pode passar por não usar nenhuma das palavras. Mitigação: as exceções de
  metadado/agregado, os testes de não-regressão (a suíte cobre explicitamente as
  perguntas que **não** podem ser barradas) e o `GUARDRAILS_ENABLED` para
  depurar.
- **[Médio impacto]** Mexer em `query_system.md` é mexer em **prompt calibrado**
  da Persona 2 — o [ADR-0012](./0012-desenvolvimento-orientado-a-metricas-no-retrieval.md)
  exige medir antes/depois. Risco ativo até o eval rodar.
- **[Baixo impacto]** Mais um lugar a manter quando o vocabulário do domínio
  muda; parcialmente resolvido pelo `GUARDRAIL_EXTRA_PII_TERMS` em env.

### Avaliação

Os ganhos superam. O que se compra é a propriedade que a camada de prompt nunca
dá sozinha — a recusa que não depende de o modelo cooperar — pelo preço de uma
lista de padrões que precisa de manutenção. O risco que permanece ativo é o do
prompt calibrado: a mudança em `query_system.md` **não está medida** e o gate do
ADR-0012 é pré-requisito de merge.

## Consequências

- **Positivas**: as três regras pedidas passam a valer nas duas personas, com
  teste no host; pergunta fora do objetivo deixa de consumir a pipeline;
  a resposta sobre privacidade vira artefato revisável, não improviso.
- **Negativas**: superfície nova de falso positivo; o baseline da Persona 2 fica
  pendente de nova medição.
- **Ações decorrentes**:
  1. Rodar o gate do ADR-0012 (`make eval-quick`, e o eval ao vivo da Persona 2)
     comparando com o baseline antes de mergear — a mudança em `query_system.md`
     é comportamento, não formatação.
  2. Confirmar o **acesso mascarado** no banco analítico. É ele, e não este ADR,
     que protege o valor; o guardrail protege a *conversa*.
  3. Fechar a lacuna de veracidade da autocorreção: no caminho do `Agent`
     (`McpSqlExecutor`, só quando o banco recusa a consulta), o resultado da
     ferramenta volta para o contexto do modelo — ou seja, na tentativa
     corrigida ele **vê linhas**. O caminho normal (`MCP_DIRECT_FIRST`) não passa
     por lá, e a mensagem de `data_access` foi escrita para ser verdadeira nos
     dois casos, mas a afirmação só fica incondicional quando o retorno bem
     sucedido da ferramenta for truncado antes de entrar no contexto.
  4. Alimentar os padrões conforme aparecerem recusas erradas em uso real; cada
     ajuste entra com o seu caso no `tests/test_guardrails.py`.

## Validação em ambiente real (2026-08-31)

**50 chamadas manuais** contra a stack de pé (contrato de chat, o mesmo do
open-webui), nas duas personas: **50/50** conforme o esperado — 22 de bloqueio
(PII, acesso ao dado, fora de escopo, sobre o sistema) e 28 de passagem, estas
com SQL executado, leitura do resultado e gráfico. Três defeitos foram
encontrados **pelos testes reais** e corrigidos:

1. **CNPJ de pessoa jurídica bloqueava** — barrava um caso do gold de negócio.
   Corrigido conforme a regra de pessoa jurídica acima.
2. **`refusal` usado para falta de dado.** O modelo devolvia `refusal: "escopo"`
   quando o schema recuperado não cobria o assunto, e o render de recusa
   suprimia o fallback "tabelas disponíveis no contexto" — a pessoa via uma
   negativa seca no lugar das tabelas que existem. Os prompts passaram a dizer
   que `refusal` é só para PII e fora-do-objetivo: **falta de dado não é
   recusa**.
3. **Verbos de posse faltando em `data_access`.** "Vocês **armazenam** os dados
   que eu pergunto?" escapava do gate e recebia um panorama de catálogo como
   resposta. `guardar/armazenar/salvar/gravar/registrar/treinar` entraram na
   regra, com `voces?` no plural.

Cada correção entrou com o seu caso em `tests/test_guardrails.py`.

## Referências

- [ADR-0010](./0010-camada-de-entrega-tabelas-grafico-explicacao-relatorio.md) — o
  `ResultExplainer` lê um digest estatístico, não as linhas: é a decisão que
  torna verdadeira a frase "o modelo não vê o dado".
- [ADR-0013](./0013-execucao-via-postgres-mcp-e-grafico-chart-spec-no-v1.md) —
  execução read-only via postgres-mcp, fora do modelo.
- [ADR-0014](./0014-personas-e-modos-de-uso-do-assistente.md) — duas personas
  explícitas em vez de classificador de intenção; o mesmo argumento sustenta o
  gate determinístico deste ADR.
- [ADR-0015](./0015-amostra-de-dados-reais-no-modo-explorar.md) — a amostra de
  linhas reais, que é o que muda o perfil de risco da resposta.
- `src/rag/guardrails/` (política + textos), `tests/test_guardrails.py` (contrato).
