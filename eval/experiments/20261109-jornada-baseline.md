# Jornada (Assistente, ADR-0020): primeira medição ponta a ponta — 09/11/2026

- **Commit avaliado**: `4eee0ed` (branch `feat/jornada-assistente`)
- **Ambiente**: local, escopo `cidades` (197 tabelas), postgres-mcp direto no banco
  `10.0.0.50`, `gpt-4o`, `JOURNEY_POLICY_SERIES=true`
- **Dataset**: `eval/datasets/journey/cidades-journey-v0.1.0.json` — **draft**, 21 casos,
  sem revisão de domínio. É diagnóstico, não gate.
- **Run**: `eval/reports/journey-20261109/` (fora do git): `run.json` + uma transcrição
  `.md` por caso, com as imagens em `img/`.
- **Como repetir**: `make eval-journey` (chama o LLM; ~6 min).

## 1. Precisava rodar os evals antigos?

**Não. O gate do ADR-0012 não se aplica**, e isso foi conferido no diff (`git diff
4eee0ed~1 4eee0ed`), não presumido:

| o que o gate cobre | mudou no commit? |
|---|---|
| retrievers, cards, embedding, poda, `EXPANSION_MODE` | não |
| `pipelines/query`, `pipelines/delivery`, `pipelines/explore` | não |
| prompts medidos (`query_*`, `explore_*`, `result_summary_*`) | não |
| `settings/configs.py` | só campos `JOURNEY_*` novos, nenhum default mudou |

O prompt que mudou é **novo** (`jornada_frase_system/user`) e só escreve a frase da
População geral, depois do SQL executado. Ele não toca recuperação nem geração de SQL,
por isso não cabe no `eval.retrieval` nem no `eval.answer`. O eval de Explorar também
não muda: a jornada só troca o `next_step` no próprio wrapper.

O que **faltava** era medir o que a jornada acrescenta. Daí o `eval/journey/`.

Regressão: suíte inteira verde (host 184 + 7 novos; container 423/424 — a única falha
é o teste de compose, que lê o `docker-compose.yaml` e roda no host).

## 2. O que o eval da jornada mede

Ele simula o OpenWebUI. Faz a pergunta, depois o pedido de follow-ups (`### Task:`),
depois clica em **cada** botão, na ordem sugerida, com o histórico acumulando. Os eixos
ficam separados, sem score composto:

| eixo | resultado |
|---|---|
| rota número/assunto/recusa correta | **21/21 (1,0)** |
| desfecho esperado | 17/21 (0,81) |
| pergunta de número chegou a um número | 9/13 (0,69) — as 4 falhas são do motor, ver §3.C |
| frase do LLM aceita pelas guardas | 8/9 (0,89); 1 caiu em "total atribuído a um item" |
| número da frase fora dos fatos | **0** |
| trilhas abertas sem erro | **46/46** (técnica 10, política 9, gestor 9, história 9, baixar 9) |
| botão já clicado volta a aparecer | 0 |
| emoji vazado / exceção | 0 / 0 |
| links de download (CSV/.docx) | **30/30** respondem 200 |
| latência da 1ª resposta | p50 **5,2 s**, p95 10,3 s |
| latência das trilhas | < 1 s, exceto política: p50 4,5 s, máx 16,9 s (SQL da série) |
| maior resposta | 301 KB (gestor; o stream em 16 KB dá conta) |

⚠️ "0 números fora dos fatos" **não quer dizer número certo**. O LLM é fiel aos fatos,
mas nos casos A e B abaixo os próprios fatos estão errados. A métrica não pega erro
que vem de `facts.py`. Isso só aparece lendo as transcrições.

## 3. Achados, do mais grave para o menos

### A. Fração exibida como "0,1" (INCC-M) — **número errado na tela**
`ouro_conjuntura_incc_m.var_12_meses` vem como fração (0,0610 = 6,1%). O `fmt` arredonda
para "0,1" no cartão, no eixo do gráfico (todos os ticks viram "0,1") e no digest.
Daí o LLM escreve, fiel aos fatos: *"começou e terminou o ano com a mesma variação de
0,1"*. Na verdade foi de 6,8% para 6,1%, com pico de 7,5%. A unidade não vem do
catálogo (a referência só traz a descrição da tabela). Precisa de regra para
`var_*`/`pct_*`/`taxa_*` com |valor| < 1 virar percentual, e o arredondamento tem de
manter algarismo significativo.
→ `transcrições: serie-incc.md`

### B. Linha de total somada como categoria (OGU) — **total em dobro**
`ouro_conjuntura_ogu` tem uma linha `acao_nome = 'SOMA'`. Ela entra na soma: o cartão
diz **R$ 39 bi**, quando o pago é ~R$ 19,5 bi. O pictograma diz *"SOMA concentra 5 em
cada 10 reais"*. É o mesmo defeito da tabela-mapa com UF + região (ADR-0020 §5), só
que em categoria. A guarda atual só cobre região. Outro detalhe: a dimensão escolhida
foi o código (`00XF`), não o nome (`FUNDO SOC.`).
→ `cat-ogu.md`

### C. Executor perde a conexão do MCP na autocorreção (afeta também o Indicador)
Em 3 dos 4 casos sem número, o SQL direto foi recusado e o `Agent` de autocorreção
falhou com `Not connected to an MCP server` (8 ocorrências no log). O agente é
construído uma vez e guardado em `McpSqlExecutor._agent`. A hipótese mais provável é
que a sessão SSE do toolset caia e não reconecte. **Não confirmado.** Quando a
autocorreção rodou, ela tentou `"Cidades".prata…` (cross-database). A causa de fundo é
a já conhecida: o catálogo aponta `prata.prata_far_historico_empreendimento`, que não
existe no banco.
→ `escalar-far-uh.md`, `rural-regiao.md`, e a série da política em `uf-far-empreendimentos.md`

### D. Guardrail recusa a pergunta que o próprio Assistente sugeriu
Na rota assunto ("Quais bases existem sobre crédito imobiliário?"), o botão sugerido
*"Qual foi o valor das concessões de crédito imobiliário para pessoa física em janeiro
de 2023?"* volta como **recusa de dado pessoal**: `\bpessoa fisica\b` está na lista de
PII do `guardrails/policy.py`. "Por mês" passa e "em janeiro de 2023" não, porque não
é marca de agregação. O defeito é anterior à jornada (ADR-0016), mas é a jornada que o
coloca num clique. "Pessoa física" como **segmento** (PF × PJ) é vocabulário do BACEN,
não pedido de PII.
→ `assunto-credito.md`

### E. Política: a varredura do catálogo não olha o programa da resposta
`catalog_probe.policy_stages` e `complementary` varrem o **escopo inteiro**. Então:
- "Lei" é sempre `ouro_ts_marco_portaria` + `prata_fnhis_propostas`, inclusive para
  FAR, CAGED e OGU. "Programa" do CAGED aponta Trabalho Social;
- "Para quem?" (gênero, emprego, renda) sai **idêntico nas 9 respostas**, e o convite
  "pergunte o mesmo número separado por um desses recortes" leva a colunas que a tabela
  da resposta não tem.

O diagrama fica constante e passa a impressão de um vínculo que não existe. A parte
honesta funciona bem: lacuna de raça/cor, nenhuma afirmação causal, série que falhou
explicada e SQL tentado mostrado. Como os dados de política ainda não estão
consolidados, o ajuste barato é **restringir a varredura às tabelas do mesmo programa**
(tokens do nome da tabela usada: `far`, `fds`, `fnhis`, `ts`, `rural`, `pro_moradia`) e
deixar o resto como "lacuna". É melhor que um "no catálogo" genérico.
→ `*/Contexto da política` em qualquer transcrição

### F. Gestor do OGU recomenda a tabela que acabou de usar
*"As tabelas desta resposta não trazem um valor previsto… No catálogo há tabelas de
orçamento que podem ajudar: `ouro.ouro_conjuntura_ogu`"*. É a própria tabela, e ela
tem `dotação` e `empenho`. O `planned_text` olha as colunas do **resultado**, não as da
tabela usada.
→ `cat-ogu.md`

### G. Ranking com LIMIT tratado como total (FDS top 10)
O cartão mostra *"10,9 mil: uhs contratadas, somando 10 categorias"* e o pictograma
*"concentra 3 em cada 10 (29% do total)"*. É a soma e a participação **dentro do top 10**,
não do FDS. O LLM escreveu certo e a guarda `misreads_total` derrubou a frase. A
determinística saiu como *"Ao todo, uhs contratadas soma 10,9 mil"*. Com `LIMIT`/`ORDER
BY … DESC`, o número em destaque deveria ser o líder, não a soma.
→ `fds-ranking.md`

### H. Menores
- **Legenda caindo no nome cru da coluna**: "captacao liquida valor", "var 12 meses",
  "construcao saldo". O LLM tende a pôr o ano na legenda, a guarda (sem dígito) a
  descarta e entra o nome da coluna sem acento.
- **CAGED**: o cartão chama o *saldo* do mês (14,1 mil) de "Empregos formais na
  construção civil". O estoque é 2,9 milhões. A trilha de política calcula "−70% no
  período" sobre um fluxo que passa por negativos, o que não tem leitura.
- **Poupança**: "captação negativa de R$ -20,3 bi" (negativo duplo) e "R$ 22,09 por
  habitante" sobre um fluxo mensal.
- **FipeZap**: o motor gerou `ORDER BY data DESC LIMIT 1` e o mês mais recente é `NULL`.
  A jornada respondeu "não consegui chegar a um número", que é correto, mas o defeito é
  do SQL (falta `WHERE … IS NOT NULL`).
- **Clique órfão** ("Números para decisão" como 1ª mensagem): a resposta pede uma
  pergunta, mas `follow_ups` devolve os 5 botões de novo, e cada um leva à mesma
  mensagem. `service.follow_ups` deveria devolver `[]` quando `marker.ref` é vazio.
- **Sem número → sem botão**: as respostas honestas (déficit, desigualdade racial)
  terminam sem nenhuma saída clicável. Um botão "o que vocês têm sobre …?" fecharia o
  caminho que o texto sugere.
- **Recusa de PII cita o "modo Explorar"**, que no Assistente não é algo que a pessoa
  escolhe.
- **.docx**: a última página leva uma linha só ("Pergunte, por exemplo…") e a descrição
  da tabela sai com sublinhado cru (`_Uma linha por…_`).

## 3.1 Diagnóstico de A, B e C (investigação do mesmo dia)

**Nenhum dos três é erro de prompt nem de modelo.** O LLM foi fiel aos fatos nos três
casos: ele não vê as linhas (ADR-0010) e escreveu exatamente o que `facts.py` lhe deu.
Trocar de modelo ou reescrever `jornada_frase_*` não mudaria nada.

### A (INCC): unidade inconsistente na origem + arredondamento nosso

Dados reais de `ouro.ouro_conjuntura_incc_m` em 2025:

| coluna | jan/25 | dez/25 | escala real | catálogo diz |
|---|---|---|---|---|
| `var_mes` | 0,71 | 0,21 | **ponto percentual** (0,71%) | "Variação percentual…" |
| `var_12_meses` | 0,0685 | 0,0610 | **fração** (6,85%) | "Variação percentual…" |
| `var_fonte_12_meses` | 6,85 | 6,10 | ponto percentual | "Variação acumulada…" |

A causa está no dbt (`data-application-cidades/.../ouro/ouro_conjuntura_incc_m.sql`).
`var_mes` vem pronto da FGV, em %. `var_ano` e `var_12_meses` são recalculados como
`indice / indice_anterior - 1`, ou seja, em fração. As três colunas são descritas como
"percentual". **Duas escalas na mesma tabela, com a mesma descrição.**

Daí, duas falhas:
1. **Origem**: o catálogo promete percentual e entrega fração. A regra da jornada é "a
   unidade vem do catálogo" (`units.py`), e o catálogo está errado. Correção na fonte: o
   dbt multiplica por 100, ou a descrição passa a dizer "em fração (0,061 = 6,1%)".
2. **Nossa**: `fmt.compact` arredonda tudo abaixo de 1 para **1 casa** (`0,0610 → "0,1"`,
   `0,0685 → "0,1"`). Apaga a informação, qualquer que seja a unidade. Foi isso que
   produziu a frase "começou e terminou com a mesma variação". Correção no RAG: manter
   2–3 algarismos significativos abaixo de 1.

⚠️ A regra que §3.A sugeria ("`var_*` com |valor| < 1 vira percentual") **está errada**:
`var_mes = 0,21` é 0,21%, e a regra o transformaria em 21%. Não dá para deduzir a escala
pelo valor. Ela tem de vir do catálogo.

### B (OGU): linha de total por desenho na origem + guarda nossa só para UF

O `ouro_conjuntura_ogu.sql` faz `UNION ALL` de uma linha `'SOMA'` **de propósito** ("no
mesmo formato da tabela do boletim, com linha SOMA"). A descrição da tabela no catálogo diz
"**Uma linha por ação de governo**" e não menciona a linha de total. O motor da P2 gerou
`SUM(pagamento) GROUP BY codigo, nome` sem filtrar `SOMA` (não tinha como saber), e
`facts._aggregate` somou as 8 linhas: 19,5 + 19,5 = **R$ 39 bi**.

Do nosso lado, duas falhas:
1. A exclusão de linha de total (`_NIVEL_AGREGADO`) só roda quando a dimensão é **UF**. Em
   categoria não roda, e "soma" nem está no conjunto.
2. `_pick_dimension` pega a **primeira** coluna de texto. Era o código (`00XF`), não o nome
   (`FUNDO SOC.`).

Correção na fonte: a tabela de uso analítico não deveria ter linha de total, porque o
formato do boletim já existe em `ouro_conjuntura_boletim_p6_ogu`. Se a linha ficar, a
descrição tem de declará-la. No RAG: estender a exclusão de linha de total a qualquer
dimensão categórica (`soma`, `total`, `total geral`) e preferir coluna `*_nome` a
`*_codigo`. O Indicador mostra a mesma linha `SOMA`, mas não soma nada, então lá o erro é
de leitura e não de número.

### C (MCP): só a PRIMEIRA autocorreção de cada processo funciona — confirmado

A causa é uma incompatibilidade entre as duas bibliotecas fixadas, não rede e não o
postgres-mcp:

1. `haystack-ai==3.0.0`: a cada `Agent.run`, o agente faz `_spawn_tools(self.tools)`,
   que chama `Toolset.spawn()`, uma **cópia rasa** (`copy.copy`) "para isolar o estado da
   execução".
2. `mcp-haystack==1.4.0`: `MCPToolset` não sobrescreve `spawn`, então a cópia
   **compartilha o `_worker`** (a sessão SSE). E `MCPToolset.__del__` chama `close()`,
   que chama `self._worker.stop()`.
3. Fim do `Agent.run`: a cópia sai de escopo, o `__del__` para o worker **compartilhado**,
   e `client.aclose()` põe `session = None`. A próxima chamada da tool, no agente que o
   `McpSqlExecutor` guardou em `self._agent`, levanta `Not connected to an MCP server`
   antes de sair do processo. A lib só tenta reconectar em `ClosedResourceError`, não com
   sessão `None`.

Reprodução (no container, sem LLM): sessão viva → `_spawn_tools(ts)` → `del` + `gc` →
`session=None` e a chamada falha. Com o LLM, o mesmo SQL errado duas vezes no mesmo
executor: **R1 autocorrige e devolve 383 linhas; R2 falha** com `Not connected`, e o agente
ainda gasta 3 passos de LLM batendo na sessão morta. A hipótese anterior, de que a sessão
SSE cairia sozinha, **foi descartada**: sem `Agent.run` no meio, a mesma sessão ficou viva
e respondeu após 320 s ociosos. O log do postgres-mcp bate com isso:
depois do `cross-database` das 16:26:22, nenhuma chamada do agente chegou ao servidor.

**Alcance**: `haystack-ai 3.0.0` está fixado desde o primeiro commit, então isso vale
desde sempre, para **toda instância de vida longa**: o `delivery` do Indicador, a jornada
e a autocorreção em produção. O defeito ficou escondido porque o caminho direto
(`MCP_DIRECT_FIRST`) resolve quase tudo e porque, em dev, o `--reload` recria as pipelines
toda hora. A amostra do Explorar escapa, porque cria um executor novo por chamada.

**Agravante visto no mesmo log**: depois de um tempo ocioso, o pool do postgres-mcp
entrega uma conexão morta (`SSL SYSCALL error: EOF detected`, 17:44:14). A primeira
consulta depois do ócio falha no caminho direto e cai na autocorreção, que, se já foi
usada uma vez, está morta. Em produção, com pouco tráfego, a primeira pergunta depois de
um intervalo pode sair sem número por causa disso.

**Produção tem o mesmo defeito.** Mesmo teste, rodado só em leitura no `hayhooks-mcid` da
10.0.0.210: sessão viva → cópia descartada → `Not connected`. Lá ele quase não aparece por
dois motivos. A autocorreção roda pouco (2 vezes em 3 semanas de log). E o momento em que a
cópia é liberada depende do coletor de lixo do Python: na de 04/11, o agente guardado ainda
chegou ao banco. É um defeito intermitente, não um ambiente diferente.

**Corrigido em `mcp_sql_executor.py`:**
- (a) `_toolset_cls()` é um `MCPToolset` cujo `spawn()` devolve uma cópia que **não é dona**
  da sessão (`copia._worker = None`). A cópia chama a tool pelo client e só o original fecha.
- (b) `_get_agent` reconstrói o agente se a sessão estiver fechada, por exemplo depois de um
  restart do servidor.

Testes em `tests/test_mcp_executor_session.py`. Com a classe original da lib, o teste do
`spawn` falha. Validação real: três autocorreções seguidas no mesmo executor dão 383 linhas
nas três. No rerun da jornada (`eval/reports/journey-20261109-mcpfix/`), as consultas do
agente agora chegam ao banco. O que sobra é erro do SQL (`Failed to parse SQL statement`),
porque o agente tenta qualificar `Cidades.cidades.prata.prata_far_historico_empreendimento`,
uma tabela que o catálogo aponta e o banco não tem. Isso é o defeito de catálogo, não do MCP.

## 4. O que está bom (e deve ser preservado)

- Rota 21/21. Guardrails e borda ("como esse dado foi calculado para SP?" é pergunta
  nova, não clique) se comportam como o ADR descreve.
- O estado entre cliques funciona: a mesma `ref` em todas as trilhas, `vistas`
  acumulando e nenhum botão repetido. O relatório traz exatamente as seções abertas,
  na ordem.
- Infográficos legíveis no celular: mapa, barras empilhadas por região, cartões de KPI.
- Nada de número inventado pelo LLM. Quando não há dado, a resposta diz que não há.

## 5. Próximos passos sugeridos

1. Corrigir A e B em `journey/facts.py`/`fmt.py`, cada um com seu caso em
   `tests/test_journey_rules.py`, e repetir `make eval-journey`.
2. Investigar C (reconexão do `MCPToolset` no `McpSqlExecutor`). Isso afeta o Indicador.
3. D no guardrail: "pessoa física/jurídica" como segmento, com caso no `test_guardrails.py`.
4. E/F na política e no gestor: varredura restrita ao programa; `planned_text` olhando
   as colunas da tabela usada.
5. Revisar o dataset com alguém de domínio e sair do `draft`. Acrescentar asserções
   determinísticas por caso (ex.: "headline do OGU ≈ R$ 19,5 bi", "INCC em %"), para
   A e B virarem métrica e não só leitura.
