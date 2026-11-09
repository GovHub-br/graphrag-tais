# ADR-0020: Jornada única — População geral como resposta padrão e personas por botão

- **Status**: Proposto (revisa a regra 2 do ADR-0014 para o novo modelo)
- **Data**: 2026-11-01
- **Autores**: Davi (graphrag-tais)
- **Revisores**: -

## Introdução ao problema

O [ADR-0014](./0014-personas-e-modos-de-uso-do-assistente.md) resolveu as duas
primeiras personas como **dois modelos no seletor** do OpenWebUI (`Indicador` e
`Explorar`), com escolha explícita e **sem roteamento automático** (regra 2).
O desenho da nova jornada do usuário (board FigJam de 25/10/2026) mudou o ponto
de partida:

- quem chega ao assistente **não sabe que persona é**, e não deveria precisar
  saber — o seletor de modelo pede uma decisão que a pessoa não tem como tomar;
- o público ficou mais largo: além de técnica e gestor, entram a **população
  geral** (quer entender e confiar no número) e o **especialista em políticas**
  (quer a política por trás do número);
- cada persona pede entregas diferentes do MESMO número — a técnica quer o SQL,
  o gestor quer indicadores e ranking, o especialista quer série histórica e
  lacunas, a população quer uma frase e um mapa.

Em 01/11/2026 a jornada foi fechada assim: **a pessoa pergunta, a primeira
resposta já sai para a População geral, e depois vêm botões para as personas
específicas.** A População geral deixa de ser botão: ela é a resposta padrão.

Restrições herdadas: o caminho medido da Persona 2 (motor, poda, prompt
`query_*`) não pode mudar sem o gate do [ADR-0012](./0012-desenvolvimento-orientado-a-metricas-no-retrieval.md);
o LLM não lê linhas nem faz conta ([ADR-0010](./0010-camada-de-entrega-tabelas-grafico-explicacao-relatorio.md));
guardrails e mascaramento valem para tudo que sai ([ADR-0016](./0016-guardrails-de-resposta.md),
[ADR-0018](./0018-mascaramento-de-dado-sensivel-na-saida.md)); o OpenWebUI está
pinado na 0.11.0 e o tema entra só por `custom.css` + `loader.js`.

## Decisão

**Um terceiro modelo, `jornada` (exibido como "Assistente"), padrão no
OpenWebUI, que responde toda pergunta no formato da População geral e oferece as
outras personas como botões. Os botões são os follow-ups nativos do OpenWebUI,
respondidos pelo próprio modelo sem LLM.**

```
pergunta ─► guardrail ─► número ou assunto? (regra, não LLM)
              │ número                         │ assunto
              ▼                                ▼
   motor da P2 (SQL) ─► executor ─► fatos   modo Explorar (P1)
              ▼                                ▼
   População geral: frase + número em       panorama + perguntas de
   destaque + pictograma + mapa + fonte     número como botões
              │
   botões ─► técnica · política · gestor · história · baixar
```

### 1. Persona padrão e trilhas

| trilha | quem | o que entrega | SQL novo? |
|---|---|---|---|
| População geral (padrão) | qualquer pessoa | 1–2 frases com o número, número em destaque com escala humana, pictograma "N em cada 10", mapa por UF (ou série, ou comparação com a média), fonte | — |
| Como esse dado foi calculado? | técnica | jornada do dado (fonte → tabela → colunas → cálculo), SQL executado, dicionário, prévia do resultado, histograma, cobertura/nulos, limitações, CSV | não |
| Contexto da política | especialista | jornada da política (lei → programa → orçamento → execução → resultado) marcada com o que tem dado, série histórica, regiões em barras empilhadas, recortes complementares (raça, gênero, emprego, renda), ressalvas, **o que o sistema não tem** | só a série |
| Números para decisão | gestor | cartões de KPI, ranking com os 3 primeiros/últimos, pontos fora da curva (IQR) ou tendência com alerta, UF × média da própria região por habitante, executado × previsto (ou a lacuna, com a tabela de orçamento que existe), relatório `.docx` | não |
| Contar a história | todos | quanto? → o que significa? → onde? → para quem? | não |
| Baixar dados e relatório | todos | CSV completo + `.docx` (links de 7 dias) + dados abertos quando houver | não |

O link do portal de dados abertos sai de `JOURNEY_OPEN_DATA_URL`. **Vazio, ele
não aparece em resposta nenhuma** — hoje o portal não existe.

### 2. Roteamento (revisão da regra 2 do ADR-0014)

A regra 2 recusou roteamento automático porque erro de roteamento é invisível e
não mensurável. A jornada aceita **dois** roteamentos, ambos visíveis:

- **número ou assunto** — regra determinística por frase (`journey/routing.py`),
  default número. Errou para assunto: a resposta traz as perguntas de número como
  botões. Errou para número: o botão "Como esse dado foi calculado?" mostra o que
  foi usado.
- **palpite de ordem dos botões** — o sistema ordena as três personas pela
  afinidade da pergunta e destaca a primeira; **todas continuam visíveis**. Quem
  decide é a pessoa.

Cada clique é logado como `[jornada/intencao]` (trilha escolhida, ordem sugerida
e posição): é o **gold de intenção** que o ADR-0014 dizia faltar para medir um
roteador. `Indicador` e `Explorar` continuam no seletor, com a regra 2 intacta.

### 3. Botões = follow-ups do OpenWebUI

Depois de cada resposta, o OpenWebUI chama o mesmo modelo com um prompt de
follow-up (`### Task: … "follow_ups"`). O wrapper reconhece esse prompt e
devolve os rótulos das trilhas, sem LLM. O clique volta como mensagem do usuário
com o rótulo exato, que `tracks.match_track` reconhece por **igualdade** do texto
normalizado (nunca por "contém": "como esse dado foi calculado para SP?" é uma
pergunta nova). Título e tags da conversa saem pela mesma via, também sem LLM.

O visual de botão vem do tema (`webui/custom.css`, seção 11): pílulas que quebram
linha, a primeira preenchida (o palpite), largura cheia no celular. Nenhum fork do
frontend.

### 4. Estado: a conversa é a memória

O OpenWebUI não manda id de conversa ao modelo. Cada resposta leva no fim um
comentário Markdown invisível (`[//]: # (tais:v1 ref=… rota=… trilha=… vistas=…)`)
e o resultado executado fica no Redis por `JOURNEY_STATE_TTL` (7 dias). O clique
reusa **o mesmo resultado** — a trilha técnica mostra o SQL que gerou O número que
a pessoa leu. Estado vencido: recalcula e avisa.

### 5. O LLM não faz conta (de novo)

Tudo o que é número sai de `journey/facts.py`, em Python: métrica (a primeira
numérica que não é identificador nem eixo de tempo), dimensão (UF, tempo ou
categoria), aditividade (média e taxa não somam), dinheiro (pela coluna somada no
SQL), total, concentração, valores fora da curva, cobertura. O único texto de LLM
na População geral é uma frase e uma legenda, com duas guardas: a frase **tem de
conter o número principal como ele foi formatado** (senão entra a frase
determinística) e a legenda **não pode ter dígito**. A escala humana vem de
tabela fixa (`config/escala_humana.yml` + população do IBGE, Censo 2022, versionada
em `journey/data/`) — o LLM nem participa da escolha.

Uma guarda nasceu do primeiro teste real: tabelas "mapa" empilham linhas de UF e
de região (N, NE, CO, S) na mesma coluna. Somar tudo conta o mesmo valor duas
vezes. Linhas de região/total saem das contas, e "SE" (Sergipe ou Sudeste?) sai
junto quando há mistura — a resposta diz isso.

### 6. Gráficos

Doze tipos novos em `components/charts/infographics.py`, todos PNG (matplotlib),
na identidade do tema, desenhados para a tela do celular: número em destaque,
pictograma, mapa por UF sobre a malha oficial do IBGE, comparação com a média,
cartões de KPI, ranking, tendência/fora da curva, histograma, cobertura, linhagem,
jornada da política e barras empilhadas por região. A série temporal reusa o
renderer da Persona 2.

## Alternativas consideradas

### Alternativa A: botões dentro da resposta (links + `loader.js`)

- Links Markdown com um esquema próprio, interceptados por JS que preenche e envia
  a caixa de mensagem.
- **Prós**: controle total do lugar e do desenho dos botões.
- **Contras**: depende do editor ProseMirror do OpenWebUI aceitar texto injetado e
  do id do botão de enviar — os dois mudam entre versões sem aviso; quebra em
  silêncio. **Rejeitada**: o follow-up é API estável do produto e já faz o clique.

### Alternativa B: Action Functions do OpenWebUI

- Funções instaladas no banco do OpenWebUI, com ícones na barra da mensagem.
- **Prós**: nativo.
- **Contras**: ícones pequenos na barra de ações, não botões com rótulo; a regra
  passaria a morar no banco do OpenWebUI, fora do repositório. **Rejeitada.**

### Alternativa C: classificador de persona por LLM antes da resposta

- **Contras**: o problema que o ADR-0014 apontou continua — erro invisível, uma
  chamada a mais antes de tudo. A jornada troca "adivinhar a persona" por "mostrar
  todas e deixar escolher". **Rejeitada.**

### Alternativa D: gráficos interativos (gpt-vis, ECharts, AntV Infographic)

- Era a sugestão do board.
- **Contras**: o OpenWebUI 0.11 não renderiza esses blocos sem mudar o frontend, e
  o mesmo gráfico precisa ir para o `.docx`. **Adiada**: PNG agora; interativo
  quando houver fork/plugin de renderização, sem mudar `facts` nem as trilhas.

### Não fazer nada

A População geral e o especialista em políticas continuam sem resposta adequada, e
quem chega precisa escolher um modo que não entende.

## Tradeoffs

### Vantagens

- **[Alto impacto]** A pessoa não escolhe persona; ninguém fica sem caminho.
- **[Alto impacto]** O caminho medido da Persona 2 segue intocado: o motor é
  chamado como caixa-preta, e nenhum default de `settings` mudou.
- **[Médio impacto]** As trilhas reusam o resultado: a técnica, o gestor, a
  história e o baixar não gastam LLM nem SQL.
- **[Médio impacto]** O log de cliques cria o gold de intenção que faltava.

### Desvantagens

- **[Médio impacto]** O contrato dos botões é o TEXTO do rótulo; renomear um
  botão quebra o clique em conversas antigas.
- **[Médio impacto]** Respostas com vários PNGs base64 ficam pesadas no histórico
  (e passam do limite de linha SSE do OpenWebUI — por isso o wrapper manda o texto
  em pedaços de 16 KB).
- **[Baixo impacto]** A série histórica depende de o motor achar uma tabela com
  data — quando o catálogo aponta uma tabela que o banco não tem, a trilha diz
  isso e mostra o SQL tentado.

## Consequências

- **Positivas**: as quatro personas do board são atendidas num modelo só; seis
  novas entregas e doze tipos de gráfico; título/tags/follow-ups do OpenWebUI
  deixam de rodar o RAG sobre o prompt de bastidor (no `jornada`).
- **Negativas**: mais uma superfície (pacote `journey`, prompt `jornada_frase_*`,
  seção 11 do tema); `escala_humana.yml` é conhecimento mantido à mão.
- **Ações decorrentes**:
  1. Publicar a imagem do tema `v0.11.0-govhub.2` (a seção 11 do CSS) e rodar
     `scripts/setup_webui_models.sh` em produção.
  2. Preencher `JOURNEY_OPEN_DATA_URL` quando o portal existir.
  3. Medir o palpite com o log `[jornada/intencao]` e, com volume, decidir se a
     ordem dos botões merece um modelo.
  4. A guarda de nível misturado (UF + região) cobre o sintoma; a causa é o SQL
     não filtrar a coluna `nivel` — vale levar ao gold do motor.

## Referências

- Board FigJam da jornada: https://www.figma.com/board/x7BFSX1TWbZk14QbVRQCRW
- [ADR-0014](./0014-personas-e-modos-de-uso-do-assistente.md) — personas como modos (regra 2 revisada aqui para o `jornada`)
- [ADR-0010](./0010-camada-de-entrega-tabelas-grafico-explicacao-relatorio.md) — o LLM não vê linhas
- [ADR-0016](./0016-guardrails-de-resposta.md) e [ADR-0018](./0018-mascaramento-de-dado-sensivel-na-saida.md) — guardrails e mascaramento
- IBGE — malhas (API v3) e Censo 2022 (SIDRA 4709)
