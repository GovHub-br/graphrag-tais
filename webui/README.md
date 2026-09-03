# Identidade visual GovHub no Open WebUI

Tema aplicado pelos **dois ganchos oficiais** do Open WebUI 0.11.0 — sem fork,
sem rebuild de imagem, sem tocar no frontend. Referência estética:
[`docs/graphrag-tais-overview.html`](../docs/graphrag-tais-overview.html) e a
skill `govhub-visual-identity`. O estudo das alternativas está em
[`docs/20260820-estudo-identidade-visual-na-ui.md`](../docs/20260820-estudo-identidade-visual-na-ui.md).

```
webui/
├── custom.css     → /static/custom.css   (link no <head>, CSS livre)
├── loader.js      → /static/loader.js    (script defer: tema fixo + INTRO da marca)
└── assets/        → ícone, favicon, splash e avatares dos modelos
```

## Como funciona

**0. Duas exceções à regra de que basta CSS sem layer.**

- **Estilo inline injetado depois.** Alguns componentes (o splash é um) injetam um
  `<style>` no fim do `<body>`. Sem layer e depois do nosso `<link>` no `<head>`,
  eles **empatam e ganham**. Solução: subir a especificidade — `html #splash-screen`
  em vez de `#splash-screen`.
- **`style=` inline no elemento.** O script do splash crava `height: 6rem` no
  atributo `style` da logo; aí só `!important` resolve.

**1. Sem `!important` no resto.** O CSS do Open WebUI é Tailwind v4 e vive inteiro dentro
de `@layer` (theme/base/utilities). Regra **sem layer** vence qualquer regra com
layer, independente de ordem e especificidade. Logo, basta redefinir os design
tokens — nada de caçar classe utilitária.

**2. A alavanca central é a escala `--color-gray-*`.** Quase toda superfície,
borda e texto do app sai dela. Trocada por neutros levemente arroxeados, o app
inteiro muda de uma vez, nos dois modos.

**3. Markdown via `--tw-prose-*`.** O conteúdo gerado usa Tailwind Typography, então
retematizar tabela, código, link e citação é redefinir essas variáveis — vale para
as tabelas de dicionário de dados e o SQL do `Indicador` sem regra específica.

**4. A Inter já vem embarcada** no Open WebUI (`@font-face` própria); o tema só a
promove a primeira do stack.

**5. ⚠️ Em telas estreitas (< 768px) o `#sidebar` é OUTRO elemento** — um drawer com
árvore e classes próprias. Regra que dependa de herança de cor funciona no desktop e
falha no mobile (foi o que deixou os rótulos pretos sobre o roxo). Por isso a cor do
rail é aplicada de forma ampla: `#sidebar, #sidebar *:not([role="menu"]) *`.
Menus flutuantes são renderizados **fora** do `#sidebar` (portal), então escapam.

## Onde a marca aparece

| Lugar | Como |
|---|---|
| **Intro** (entrada) | tela cheia no gradiente da marca: o símbolo entra pela esquerda, cai, quica três vezes no chão até a direita, volta ao centro e revela a mensagem de boas-vindas com o botão **Começar**. Ver "A intro" abaixo |
| **Barra lateral** | gradiente roxo + símbolo em branco no topo, ao lado do nome do produto |
| **Tela inicial** | lockup "Gov Hub" acima do nome da persona + símbolo gigante a 2,8% de opacidade como marca-d'água. Logo abaixo do nome vem a **mensagem de boas-vindas** — que não é arquivo daqui, é a `description` do modelo (ver "Camada de conteúdo") |
| **Carregando** | o ponto que pulsava virou o símbolo GovHub — as respostas do Indicador levam de 30 s a 1 min, é onde a pessoa mais olha. ⚠️ Dois detalhes: o componente tem três tamanhos (a resposta usa o `md`), então a regra **não** pode se prender a uma classe de tamanho; e o ponto é um `<span>` dentro de outro `<span>` `flex` — redimensionar só o de dentro deixa a marca achatada, porque como item flex ele é espremido de volta. Precisa dos dois + `flex: none` |
| **Aba / favicon / PWA** | símbolo GovHub — ⚠️ inclui o **`favicon.ico`**, não só o `.png`/`.svg`: o `index.html` declara `rel="shortcut icon"` apontando para o `.ico`, e é ele que o navegador costuma usar na aba. Sem trocar o `.ico`, a aba continua com o ícone do Open WebUI mesmo com os outros três substituídos. Os ícones são gerados **quadrados** (o símbolo é 176×202): sem isso ele sai achatado nas caixas `rounded-full` |
| **Avatar das personas** | ícones do kit (`charts`, `database`, `open-folder`) |
| **Gráfico** | roxo `#7c3aed` + Inter, no chat e no `.docx` |

## A intro

A tela roxa de entrada é **elemento nosso** (`#gh-intro`, montado pelo `loader.js`),
não o `#splash-screen` do Open WebUI. Motivo: o splash nativo é removido pelo
próprio app (`+layout.svelte`: `getElementById('splash-screen')?.remove()`) assim
que o carregamento termina — meio segundo — e não há gancho para segurá-lo. Quem
decide quando a intro sai é o **clique em "Começar"**.

O splash nativo **continua tematizado** e é o plano B: a intro só o esconde
(`html.gh-intro-on #splash-screen { visibility: hidden }`) depois de conseguir se
montar, então JS quebrado cai na tela antiga em vez de numa tela branca.

**O quique é a barra de progresso.** Enquanto o app sobe, o botão fica desabilitado
como "Preparando…"; ele vira "Começar" quando o `#splash-screen` nativo some do DOM
— que é a última coisa que o `+layout.svelte` faz ao terminar de carregar. Ou seja,
a espera é real e clicar leva mesmo a um app pronto. Um teto de 20 s arma o botão de
qualquer jeito para ninguém ficar preso numa rota de erro.

Três coisas que essa animação ensinou, e que valem para qualquer outra aqui:

- 🔴 **Aba em segundo plano não tem layout.** O navegador congela a animação de uma
  aba escondida, mas o `setTimeout` continua correndo no relógio de parede — e
  `getBoundingClientRect()`/`innerWidth` voltam **zerados**. A primeira versão
  cronometrava o fim do percurso por `setTimeout` e calculava o pouso do símbolo a
  partir dessas medidas: quem abrisse o app numa aba de fundo (Cmd+clique, sessão
  restaurada) voltava para a mensagem **sem a marca**, atirada a milhares de pixels
  dali. Agora quem manda no fim é o `animationend` (relógio da própria animação) e o
  cálculo tem guarda: geometria zerada → tenta de novo no `requestAnimationFrame`,
  que por definição não roda em aba escondida e espera sozinho a aba aparecer.
- ⚠️ **`img` dentro de caixa de posicionamento some.** O preflight do Tailwind põe
  `max-width: 100%` em toda `<img>`, e os 100% aqui são de um nó de posicionamento
  **0×0** — o símbolo era espremido a zero de largura e a intro rodava vazia. Daí o
  `max-width: none` explícito.
- 🔴 **`role="dialog"` + `aria-modal` não prendem foco nenhum.** Eles só
  DECLARAM que a tela é modal; não mexem na ordem de tabulação nem impedem o app
  de chamar `focus()` em algo atrás. E o Open WebUI faz exatamente isso: o
  `MessageInput` roda `setTimeout(() => chatInput?.focus(), 0)` ao montar, ou
  seja, **depois** de a intro estar de pé. Resultado medido antes da correção: o
  foco vivia no `#chat-input` DEBAIXO do overlay, quem digitasse na tela de
  boas-vindas enchia a caixa de mensagem invisível, e um Tab passeava pelos ~28
  controles do app escondido. A guarda é um `focusin` em captura que devolve o
  foco para dentro — e não uma armadilha de Tab, porque assim cobre os três
  caminhos de uma vez (Tab, clique e `focus()` programático). Ela é solta no
  `sair()`, que então devolve o foco ao `#chat-input` — restaurando o estado que
  existiria se a intro não estivesse ali.
- ⚠️ **Com o eixo X em `linear`, tempo é distância.** Para o primeiro baque cair no
  meio da tela, a queda tem de ocupar a mesma fração do tempo que a metade esquerda
  ocupa do percurso — por isso a queda é longa (0→40%) e os quiques, curtos.
  Encurtar a queda joga o baque para a esquerda.

**Onde mexer**: o texto da mensagem, a assinatura ("ASSISTENTE DE DADOS TAIS") e
os tempos de espera estão no `loader.js` (`MENSAGEM`, o HTML de `montar()`,
`MS_REDE`, `MS_SOCORRO`); a duração do percurso e a trajetória, no `custom.css`
(`--gh-salto` e as keyframes `gh-intro-*`). A duração vive **só** no CSS — o JS
espera o evento, não cronometra.

⚠️ A mensagem é um parágrafo **centrado** e usa `text-wrap: balance`, não
`pretty`: `pretty` só cuida da última linha e deixava "Vamos começar?" sozinha no
pé. Texto muito mais longo que o atual estoura a tela baixa — a quebra
`@media (max-height: 700px)` já encolhe símbolo e espaçamentos, mas não o texto.

⚠️ A intro aparece a **cada carregamento de página**. Para mostrá-la uma vez por
sessão do navegador, o gancho é um `sessionStorage` em volta da chamada de `montar()`.

## Montagem (docker-compose.yaml)

Os arquivos são montados em **`/app/build/static/`**, não em
`/app/backend/open_webui/static/`. Motivo: no boot, `config.py` **apaga** o
segundo e o recopia do primeiro. Montar no destino gera `Read-only file system`
no log a cada boot; montar na fonte é limpo.

⚠️ **Consequência prática: `/static` é uma cópia feita no boot.** Depois de editar
`custom.css` ou `loader.js`, é preciso recarregar o container:

```bash
docker compose restart webui
```

Para iterar rápido no tema sem reiniciar, escreva direto no destino servido
(vale só até o próximo boot):

```bash
docker cp webui/custom.css open-webui:/app/backend/open_webui/static/custom.css
```

⚠️ **E force o recarregamento no navegador** (`Cmd/Ctrl + Shift + R`). O
`/static/*` é servido por `StaticFiles` **sem `Cache-Control`**, só com
`ETag`/`Last-Modified` (`main.py`: `app.mount('/static', StaticFiles(...))`), então
o navegador aplica cache heurístico e um F5 comum costuma devolver a versão antiga
— de CSS *e* de imagem. Se o tema "não mudou", é quase sempre isto.

⚠️ **No Safari o `Cmd + Shift + R` NÃO basta** — ele revalida o documento e mantém
as subrequisições. Use **`Cmd + Option + E`** (Develop → Empty Caches) e recarregue,
ou navegação privada para conferir. E o sintoma no Safari é pior do que "tema
antigo": se o **`loader.js`** vier do cache velho ou falhar, o tema não é fixado e o
script inline do Open WebUI deixa `localStorage.theme = 'system'` — num Mac em modo
escuro o app inteiro sai escuro, que é o cenário que o `custom.css` não cobre.
Tema escuro inesperado ⇒ suspeite do `loader.js` cacheado antes de suspeitar do CSS.

## Tema claro fixo

`loader.js` fixa `theme=light`. A identidade GovHub é assumidamente clara (o
overview.html chega a forçar `color-scheme:light`), e o modo escuro do Open WebUI
não é só outra cor: troca a escala de cinza inteira e usa utilitários `dark:` com
branco cravado, que o CSS não tem como reconciliar.

## Camada de conteúdo (fica no banco, não em arquivo)

Aplicado por API e **persistido no volume `open-webui`** — não se perde no
restart, mas também não vem junto num deploy novo. Para recriar:

- **Dropdown de modelos** (ADR-0014) — `scripts/setup_webui_models.sh`, idempotente,
  governa os três estados de uma vez: `query` → **Indicador**, `explore` →
  **Explorar** e `ingest` **oculto** (`is_active: false`, que em `utils/models.py`
  vira `models.remove(model)`; a pipeline segue no Hayhooks e `/ingest/run`
  continua respondendo). O script também desliga o **Arena Model** por
  `POST /api/v1/evaluations/config`. ⚠️ Mande `access_grants: []`; com `null` o
  endpoint estoura 500 revalidando o próprio payload.
- **Mensagem de boas-vindas** — é a `meta.description` de cada persona, aplicada
  pelo mesmo `setup_webui_models.sh`. O `Placeholder.svelte` a renderiza **entre o
  nome da persona e a barra de digitação**, que é exatamente onde uma saudação
  cabe; no dropdown ela só aparece no tooltip do ℹ️, então o tom de conversa não
  atrapalha a escolha do modelo. ⚠️ O componente aplica `line-clamp-2` +
  `max-w-xl`: o que passar de **duas linhas** (~170 caracteres) é cortado com
  reticências e só reaparece no hover. Por isso o texto não repete o que a tela
  já diz — "escreva na barra abaixo" é o placeholder do input e o convite a
  começar são os cards de "Sugerido".
- ⚠️ **Avatar das personas mora no mesmo payload.** Ele é um PNG **em base64**
  dentro de `meta.profile_image_url` (os ícones do kit não estão entre os
  arquivos montados em `/app/build/static`), e o endpoint de update substitui
  `meta` **inteiro** — um update sem o avatar apaga o ícone e a persona volta a
  exibir a marca genérica. O script embute `webui/assets/govhub-icon-*.png`.
- **Perguntas sugeridas** em pt-BR: `POST /api/v1/configs/suggestions`.

`DEFAULT_PROMPT_SUGGESTIONS` está no compose para instalações novas; numa
instalação existente o valor do banco vence e o env é ignorado.

## Licença — o que foi e o que não foi feito

A licença do Open WebUI proíbe alterar/remover/ocultar o branding, com exceção
para deployments de **até 50 pessoas em 30 dias**. O que está aqui é
**co-branding**, não white-label:

- `WEBUI_NAME=TAIS GovHub` — o backend anexa **" (Open WebUI)"** sozinho
  (`env.py`: `if WEBUI_NAME != 'Open WebUI': WEBUI_NAME += ' (Open WebUI)'`) e
  **isso foi mantido de propósito**: o nome do produto continua visível na aba e
  no topo da barra lateral, que é onde a barra lateral mostra
  "TAIS GovHub (Open WebUI)". Não existe env que desligue o sufixo — tirá-lo é
  alteração de branding e exige `LICENSE_KEY` enterprise.
- O ícone do produto passou a ser a marca GovHub. Isso é alteração de branding e
  se apoia na exceção dos 50 usuários — **reavaliar antes de abrir para a rede**.
- Nome limpo e substituição oficial de estáticos exigem `LICENSE_KEY` enterprise.

## Manutenção

A imagem está **pinada em `v0.11.0`**. O tema depende dos tokens desta build;
em `:main` um `pull` silencioso pode trocar o CSS por baixo. Ao subir de versão:
conferir `--color-gray-*`, `--tw-prose-*`, `#sidebar`, `#message-input-container`
e `button.waterfall`, que são os pontos de apoio do tema. Na 0.11.0 os dois ganchos
(`<script src="/static/loader.js" defer>` e `<link href="/static/custom.css">`)
seguem no `<head>`, e o `custom.css` usa só `:has()` — nada de `oklch`/`color-mix`,
então não há piso de versão de navegador além de Safari 15.4.

A **intro** depende de um detalhe a mais do app: o `#splash-screen` sumir do DOM é
o sinal de "carregou" que arma o botão **Começar**. Se numa versão futura o Open
WebUI parar de remover esse elemento, o botão passa a armar só pelo teto de 20 s —
degrada devagar, não quebra, mas é ponto de conferência ao subir de versão.

🔴 **O pin só anda para FRENTE.** A migration `f0bd01a18a3d` entrou na 0.11.0 e o
Alembic não sabe descer de revisão: apontar um banco que já rodou 0.11.0+ para a
0.10.2 aborta o boot com `Can't locate revision identified by 'f0bd01a18a3d'` — o
container fica `healthy` e serve `/error`, que é um jeito silencioso de quebrar.
Foi o que aconteceu em prod em 25/08/2026, vindo de `:main`. Para descer de versão,
o volume `open-webui` tem de ser recriado (e aí perde-se conversa, usuário e config).
