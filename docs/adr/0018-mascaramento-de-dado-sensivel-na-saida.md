# ADR-0018: Mascaramento de dado sensível na saída, por catálogo de colunas

- **Status**: Proposto
- **Data**: 2026-09-08
- **Autores**: Davi Aguiar (GovHub / TAIS)
- **Revisores**: —

## Introdução ao problema

O [ADR-0016](./0016-guardrails-de-resposta.md) decidiu proteger a **conversa** e
deixou a proteção do **valor** para fora da aplicação. A frase que emoldurava
aquela decisão é literal:

> A restrição que emoldura a decisão: o acesso ao banco analítico vai passar a
> ser feito por um **usuário já mascarado** — a proteção de fato do dado pessoal
> é da camada de banco, não desta.

Ele avaliou explicitamente mascarar a saída (Alternativa C) e recusou, com uma
cláusula de reabertura escrita: *"Fica como opção a reavaliar **se** o acesso
mascarado não cobrir alguma base."* Este ADR é essa reavaliação.

**O que mudou**: a premissa não se sustenta como cobertura universal. O acesso
mascarado é configurado por base, no catálogo do Trino, e nem toda base do
escopo o tem — cada base nova entra no RAG antes de entrar naquele controle, e
o intervalo entre as duas coisas é justamente o período em que o sistema
responde. Enquanto isso, o sistema **já devolve linhas reais** em dois
caminhos: a amostra do modo Explorar ([ADR-0015](./0015-amostra-de-dados-reais-no-modo-explorar.md))
e a execução do SQL da Persona 2 ([ADR-0017](./0017-execucao-do-sql-via-trino-mcp.md)).

Some-se a isso um vazamento que o próprio ADR-0016 registrou como pendência e
que nenhum controle de banco resolve sozinho: **linha real entra em prompt de
LLM**. O `ChartSpecBuilder` manda três linhas de exemplo para a OpenAI a cada
gráfico, e a autocorreção do `McpSqlExecutor` devolve o retorno da ferramenta
para o contexto do agente. Um valor mascarado no banco chega mascarado ali
também — mas só nas bases que têm o acesso mascarado, que é exatamente a
lacuna.

Restrição de negócio que continua valendo, herdada do ADR-0016: **CNPJ e razão
social de pessoa jurídica são informação pública** e não podem ser tratados
como dado pessoal — bloqueá-los quebra caso do gold ("as 10 EOs de alto risco;
mostre nome, CNPJ…").

## Decisão

Mascarar o **valor** das colunas sensíveis dentro do `McpSqlExecutor`,
guiado por um **catálogo versionado** de `(schema.tabela → colunas)`, com
pseudonimização por HMAC-SHA256.

Quatro escolhas compõem a decisão:

**1. O ponto de aplicação é o executor, e só ele.** Toda linha real do sistema
passa por `McpSqlExecutor.run()` — as duas personas, o caminho direto e o do
agente. Mascarar ali é o que faz "não vaza para o usuário" e "não vaza para o
modelo" serem a mesma linha de código; a tabela do chat, o gráfico, o digest do
`ResultExplainer`, o CSV da amostra e o `.docx` recebem as linhas já
pseudonimizadas, sem que nenhum deles precise saber que a proteção existe.
Mascarar no renderer protegeria só a tela, e teria de ser repetido em cinco
lugares.

**2. O catálogo é arquivo, não env e não código.** `config/sensitive_columns.yml`
(YAML ou JSON), montado read-only no container. O mapeamento é conhecimento de
domínio, revisado por gente que não mexe em Python, e é grande demais para
caber numa variável de ambiente como o `SCOPE_GAPS`. Entra por PR, como o
resto do repositório. **Ausente ou vazio = nada mascarado**, que é o
comportamento anterior a este ADR.

**3. Pseudonimizar, não apagar.** O default é `hash`: HMAC-SHA256 com segredo
(`SENSITIVE_HASH_PEPPER`), truncado, exibido como `#a3f9c1d20b`, com escopo
**global** — o mesmo valor vira o mesmo código em qualquer tabela. Isso preserva
o que o sistema existe para fazer (`COUNT(DISTINCT)`, `GROUP BY`, cruzar duas
tabelas pela mesma pessoa) e destrói o que ele não deve fazer (dizer quem é).
`redact` (`[protegido]`) fica para o que não deve nem ser contado.

⚠️ **Hash sem segredo não é proteção**: CPF é um espaço de 10¹¹ valores, e uma
tabela arco-íris completa se constrói em minutos. Sem `SENSITIVE_HASH_PEPPER` o
sistema **não** gera hash — degrada para `redact` e registra erro. É a única
regra aqui que não é configurável.

**4. Agregação nunca é mascarada.** `COUNT(cpf)` conta pessoas sem identificar
nenhuma, e é literalmente o que a Persona 2 existe para responder. O resolvedor
lê a projeção com `sqlglot`: função de agregação passa limpa; alias e expressão
escalar (`CAST(cpf AS VARCHAR)`, `upper(nome)`) carregam a sensibilidade da
coluna de origem. Na dúvida — `SELECT *`, coluna sem qualificador em consulta
com JOIN, parse falho — **fecha em favor da privacidade**.

Duas consequências operacionais decorrem direto disso:

- **O retorno da ferramenta é mascarado antes de virar contexto do modelo.**
  É aqui que a pendência (3) do ADR-0016 fecha de vez. O agente corrige SQL
  lendo a **mensagem de erro** do banco e nomes de coluna — ele nunca precisou
  dos valores —, então mascarar o retorno preserva a autocorreção inteira em
  vez de trocar uma coisa pela outra. A mensagem de erro passa intacta (sem
  ela não há correção) e uma falha no mascaramento **fecha**: devolve erro, não
  o texto original. ⚠️ O wrapper tem de ser instalado **depois** do
  `warm_up()`: o `MCPToolset` nasce com uma tool placeholder e a substitui ao
  conectar — envolver antes é envolver o que vai ser descartado, e o log diria
  que está tudo protegido.
- **Coluna mascarada não vira eixo de gráfico.** Vinte barras rotuladas por
  hash não são um gráfico; o `ChartSpecBuilder` a descarta antes de escolher, e
  se o que sobra não dá gráfico, não há gráfico.

E uma de produto: **a resposta diz que mascarou**. Nota no chat, no CSV e
callout no `.docx`, explicando que o código é estável. Ao contrário do erro de
execução — que fica no log de propósito —, esta limitação precisa aparecer: um
`#a3f9c1d20b` sem explicação é lido como defeito do sistema.

Nada disto toca recuperação (Qdrant, Neo4j, poda, `EXPANSION_MODE`) nem
`prompts/query_system.md`. **Não dispara o gate do
[ADR-0012](./0012-desenvolvimento-orientado-a-metricas-no-retrieval.md)** — o
que é razão de projeto, não coincidência: é por isso que a proteção entra na
execução e não na geração do SQL.

## Alternativas consideradas

### Alternativa A: confiar só no acesso mascarado do banco (o status quo do ADR-0016)

- Descrição: manter a decisão anterior — a aplicação protege a conversa, o
  banco protege o valor.
- Prós: proteção no lugar certo (o dado), sem duplicação; zero custo na
  aplicação; vale para qualquer cliente do banco, não só para este RAG.
- Contras: **cobertura por base**, e o RAG passa a responder sobre uma base
  antes de ela entrar naquele controle. Não cobre o vazamento para prompt de
  LLM nas bases descobertas. É a alternativa certa no longo prazo e insuficiente
  no intervalo — que é o que este ADR endereça. **Não é substituída por este
  ADR**: continua sendo a proteção primária.

### Alternativa B: barrar coluna sensível na projeção do SQL gerado

- Descrição: instruir o modelo a não projetar coluna sensível e recusar, via
  `sqlglot`, o SQL que a projete.
- Prós: o dado nem sai do banco.
- Contras: mexe em `query_system.md`, que é prompt calibrado — **dispara o gate
  do ADR-0012** e exige nova medição de baseline, o que custa mais do que a
  proteção inteira. Recusar a consulta transforma uma pergunta legítima ("quantos
  beneficiários por UF?", que passa por uma tabela com CPF) em erro. E depende
  de o modelo obedecer, que é justamente o que um guardrail não pode pressupor
  (ADR-0016). Mascarar o valor protege sem nada disso.

### Alternativa C: classificar sensibilidade pelas `tags` do OpenMetadata

- Descrição: usar a Custom Property / tag de coluna que a ingestão já lê
  (`metadata_record_builder`) como fonte da sensibilidade.
- Prós: fonte única com o catálogo institucional; sem arquivo paralelo.
- Contras: as tags **não são persistidas** hoje (o `Neo4jGraphWriter` as
  descarta) e o escopo MCid não as preenche — adotá-la agora significaria
  implementar ingestão de tag, esperar o catálogo ser tagueado e só então ter
  proteção. Fica como **fase 2**, fundida no mesmo índice, com o arquivo
  ganhando: é ele que passa por revisão.

### Não fazer nada / manter status quo

O sistema continuaria devolvendo CPF e nome em claro na amostra do Explorar e
na execução da Persona 2, para toda base sem acesso mascarado, e mandando essas
linhas para a OpenAI no prompt do gráfico. Em um sistema de governo, sob LGPD,
essa é a que não sobrevive à primeira auditoria.

## Tradeoffs

### Vantagens

- **[Alto impacto]** Dado pessoal deixa de sair em claro mesmo onde o acesso
  mascarado do banco não chega — inclusive para dentro de prompt de LLM, que
  nenhum controle de banco cobriria nessas bases.
- **[Alto impacto]** Ponto único de aplicação: a proteção não depende de cada
  renderer, formato de saída ou feature futura lembrar dela. Feature nova que
  consuma linhas já nasce coberta.
- **[Médio impacto]** Pseudônimo estável preserva a capacidade analítica —
  contar distintos, agrupar e cruzar continuam funcionando sobre o dado
  mascarado.
- **[Médio impacto]** Fecha a pendência (3) do ADR-0016 (autocorreção vendo
  linhas) sem trabalho adicional.
- **[Baixo impacto]** Custo de latência desprezível: nulo com catálogo vazio
  (nem o SQL é parseado), poucos milissegundos com catálogo cheio, contra
  segundos de LLM e execução.

### Desvantagens

- **[Alto impacto]** **Duplica** a proteção que o acesso mascarado dá — o contra
  que o ADR-0016 levantou, e ele continua verdadeiro. Duas fontes de verdade
  sobre o que é sensível podem divergir, e a da aplicação é a que ninguém
  audita.
- **[Médio impacto]** O catálogo é mantido à mão: coluna sensível que ninguém
  listou **não é protegida**, e o sistema não tem como saber disso. A proteção
  vale exatamente o que vale a revisão do arquivo.
- **[Baixo impacto]** ~~Perde-se a autocorreção nas tabelas sensíveis.~~
  **Resolvido em 2026-09-08**, e a história merece registro porque a primeira
  solução estava errada. O desenho inicial *inibia* a autocorreção nas tabelas
  do catálogo; o teste ponta a ponta mostrou o custo verdadeiro — a checagem é
  pela TABELA, então até `SELECT anoprojeto, COUNT(*) FROM sac__projetos`
  perdia a correção, numa tabela central do SALIC, e num catálogo do tamanho do
  MinC errar SQL não é raro. Trocar autocorreção por privacidade era um falso
  dilema: mascarar o **retorno da ferramenta** dá as duas. `SENSITIVE_INHIBIT_AUTOCORRECT`
  fica como cinto além do suspensório, default `false`.
- **[Baixo impacto]** Mascarar errado estraga amostra legítima — mitigado pelo
  `SENSITIVE_MASK_DRY_RUN`, que calibra o catálogo em produção sem alterar
  valor.
- **[Baixo impacto]** Superfície nova entre execução e entrega, num caminho que
  hoje é simples.

### Avaliação

Os ganhos superam os custos **enquanto** o acesso mascarado não cobrir todas as
bases do escopo — e só por isso. A duplicação é real e assumida: esta camada é
uma rede sob a proteção primária, não um substituto dela, e o ADR-0016 continua
valendo como está. O risco ativo a mitigar é o **catálogo desatualizado**: uma
proteção que depende de lista manual dá a sensação de cobertura que ela não
necessariamente tem, e é por isso que a fase 2 (tags do OpenMetadata) importa —
ela troca a lista manual por metadado do catálogo institucional.

Se e quando o acesso mascarado cobrir todas as bases, a decisão certa é
**esvaziar o catálogo**, não remover o código: o custo de mantê-lo desligado é
zero, e ele volta a ser útil na próxima base nova.

## Consequências

- **Positivas**: valor de coluna sensível não sai do sistema em claro por
  nenhum caminho (chat, CSV, `.docx`, gráfico, prompt de LLM); o mapeamento
  vira artefato revisável em PR; a resposta explica a limitação em vez de
  parecer defeituosa.
- **Negativas**: duas fontes de verdade sobre sensibilidade; manutenção manual
  do catálogo. (A perda da autocorreção, prevista aqui, foi resolvida pelo
  mascaramento do retorno da ferramenta — ver Tradeoffs.)
- **Ações decorrentes**:
  1. **Preencher `config/sensitive_columns.yml`** com o mapeamento revisado —
     sem ele nada disto tem efeito, e o sistema roda como antes.
  2. **Gerar e distribuir `SENSITIVE_HASH_PEPPER`** (`openssl rand -hex 32`),
     fora do versionamento. Sem ele o mascaramento degrada para redação.
  3. Rodar com `SENSITIVE_MASK_DRY_RUN=true` até os logs mostrarem que o plano
     só pega o que deveria; então desligar o dry-run.
  4. Reconferir o **acesso mascarado no banco** base a base — é ele a proteção
     primária, e cada base coberta por ele é uma linha a menos neste catálogo.
  5. Fase 2: persistir as `tags` de coluna do OpenMetadata no Neo4j e fundi-las
     ao índice, com o arquivo prevalecendo.
  6. **[decidido em 2026-09-08]** Marcador de raça/etnia e deficiência fica
     **fora** do catálogo, revertendo o default do inventário. São dado pessoal
     sensível pela LGPD art. 5º II, mas mascará-los custaria o recorte de
     equidade (beneficiários por raça/cor, por PCD) — o indicador de política
     pública que este sistema existe para produzir — sem ganho real de
     privacidade: com meia dúzia de valores distintos, o hash cai por análise
     de frequência. Quem identifica é o documento, e ele segue mascarado na
     mesma tabela. São 10 nomes, 33 entradas; reverter é
     `--excluir-natureza revisar` no importador.
  7. Reavaliar o `SENSITIVE_MASK_SCOPE`: `global` foi escolhido pela utilidade
     analítica; se a correlação entre colunas virar preocupação, `column` está
     pronto.

## Validação em ambiente real (2026-09-08)

Contra o Trino do MinC, com o catálogo importado do inventário do dbt. As
consultas rodaram pelo `McpSqlExecutor` de produção; **nenhum valor em claro foi
impresso** — o que se mediu foi fato derivado, no mesmo princípio do `digest` do
ADR-0010.

| o quê | resultado |
|---|---|
| `SELECT cgccpf, idprojeto FROM bronze.sac__projetos LIMIT 50` | 50/50 valores com cara de documento → **0/50**; nenhum valor cru sobreviveu; `idprojeto` intacta |
| distintos preservados | 49 distintos antes → **49 depois** (o pseudônimo é estável) |
| `COUNT(DISTINCT cgccpf)` (276.887 linhas) | `masked_columns=[]` — **102.676 proponentes**, indicador intacto |
| envelope da amostra (Persona 1) sobre `agentes__agentes` | `masked_columns=['cnpjcpf','cnpjcpfsuperior']`, 0/10 com cara de documento |
| tabela fora do inventário | linhas idênticas com e sem mascaramento |
| resposta completa do chat com coluna sensível | nota de transparência renderizada; documento não aparece no Markdown final; gráfico corretamente suprimido |
| base hoje carregada no Neo4j/Qdrant (MCid, 74 tabelas / 1.215 colunas) | **0 colisões** com o catálogo do MinC — as respostas de hoje não mudam |
| guardrail do ADR-0016 na API viva | 7/7 (5 recusas corretas + 2 que não podiam ser recusadas) |
| autocorreção com SQL errado de propósito (`cpf_cnpj` → `cgccpf`) | agente corrigiu sozinho; retorno mascarado **antes** do contexto; `exec_ok=True`, `masked_columns=['cgccpf']`, sem hash de hash |
| suíte | 316 testes no container, todos verdes |

Uma confirmação de projeto veio daqui: no Trino, cada tabela do dbt aparece em
**três** schemas (`bronze`, `salic`, `salic_bronze`). A chave curta do catálogo
cobre os três; um catálogo qualificado por schema teria protegido um terço.

## Referências

- [ADR-0010](./0010-camada-de-entrega-tabelas-grafico-explicacao-relatorio.md) —
  o digest determinístico: o LLM já não vê as linhas ao explicar o resultado.
- [ADR-0015](./0015-amostra-de-dados-reais-no-modo-explorar.md) — o primeiro
  conteúdo do sistema que não é metadado; registra "sem filtro de PII na saída".
- [ADR-0016](./0016-guardrails-de-resposta.md) — a decisão que este ADR reabre
  (Alternativa C) e as três pendências, uma das quais fica fechada aqui.
- [ADR-0017](./0017-execucao-do-sql-via-trino-mcp.md) — quem executa o SQL e
  segura a credencial.
- `tests/test_sensitivity.py` — o contrato: o que tem de ser mascarado e,
  igualmente, o que não pode ser.
