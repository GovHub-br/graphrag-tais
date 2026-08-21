# Identidade visual GovHub no Open WebUI

Tema aplicado pelos **dois ganchos oficiais** do Open WebUI 0.10.2 — sem fork,
sem rebuild de imagem, sem tocar no frontend. Referência estética:
[`docs/graphrag-tais-overview.html`](../docs/graphrag-tais-overview.html) e a
skill `govhub-visual-identity`. O estudo das alternativas está em
[`docs/20260820-estudo-identidade-visual-na-ui.md`](../docs/20260820-estudo-identidade-visual-na-ui.md).

```
webui/
├── custom.css     → /static/custom.css   (link no <head>, CSS livre)
├── loader.js      → /static/loader.js    (script defer, roda antes do SvelteKit)
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
| **Splash** (entrada) | tela cheia no gradiente da marca, logo vertical em negativo subindo, assinatura "ASSISTENTE DE DADOS" |
| **Barra lateral** | gradiente roxo + símbolo em branco no topo, ao lado do nome do produto |
| **Tela inicial** | lockup "Gov Hub" acima do nome da persona + símbolo gigante a 2,8% de opacidade como marca-d'água |
| **Carregando** | o ponto que pulsava virou o símbolo GovHub — as respostas do Indicador levam de 30 s a 1 min, é onde a pessoa mais olha. ⚠️ Dois detalhes: o componente tem três tamanhos (a resposta usa o `md`), então a regra **não** pode se prender a uma classe de tamanho; e o ponto é um `<span>` dentro de outro `<span>` `flex` — redimensionar só o de dentro deixa a marca achatada, porque como item flex ele é espremido de volta. Precisa dos dois + `flex: none` |
| **Aba / favicon / PWA** | símbolo GovHub — ⚠️ inclui o **`favicon.ico`**, não só o `.png`/`.svg`: o `index.html` declara `rel="shortcut icon"` apontando para o `.ico`, e é ele que o navegador costuma usar na aba. Sem trocar o `.ico`, a aba continua com o ícone do Open WebUI mesmo com os outros três substituídos. Os ícones são gerados **quadrados** (o símbolo é 176×202): sem isso ele sai achatado nas caixas `rounded-full` |
| **Avatar das personas** | ícones do kit (`charts`, `database`, `open-folder`) |
| **Gráfico** | roxo `#7c3aed` + Inter, no chat e no `.docx` |

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
`/static/*` é servido **sem `Cache-Control`**, só com `ETag`/`Last-Modified`, então
o navegador aplica cache heurístico e um F5 comum costuma devolver a versão antiga
— de CSS *e* de imagem. Se o tema "não mudou", é quase sempre isto.

## Tema claro fixo

`loader.js` fixa `theme=light`. A identidade GovHub é assumidamente clara (o
overview.html chega a forçar `color-scheme:light`), e o modo escuro do Open WebUI
não é só outra cor: troca a escala de cinza inteira e usa utilitários `dark:` com
branco cravado, que o CSS não tem como reconciliar.

## Camada de conteúdo (fica no banco, não em arquivo)

Aplicado por API e **persistido no volume `open-webui`** — não se perde no
restart, mas também não vem junto num deploy novo. Para recriar:

- **Nomes, descrições e ícones das personas** (ADR-0014) — `query` → **Indicador**,
  `explore` → **Explorar**, `ingest` → **Ingestão**, cada um com um ícone do
  `govhub-visual-identity/references/icons/`:
  `POST /api/v1/models/model/update` (⚠️ mande `access_grants: []`; com `null` a
  0.10.2 estoura 500 revalidando o próprio payload).
- **Perguntas sugeridas** em pt-BR: `POST /api/v1/configs/suggestions`.

`DEFAULT_PROMPT_SUGGESTIONS` está no compose para instalações novas; numa
instalação existente o valor do banco vence e o env é ignorado.

## Licença — o que foi e o que não foi feito

A licença do Open WebUI proíbe alterar/remover/ocultar o branding, com exceção
para deployments de **até 50 pessoas em 30 dias**. O que está aqui é
**co-branding**, não white-label:

- `WEBUI_NAME=Assistente de Dados GovHub` — o backend anexa **" (Open WebUI)"**
  sozinho (`env.py:842-844`) e **isso foi mantido de propósito**: o nome do
  produto continua visível na aba e no topo da barra lateral.
- O ícone do produto passou a ser a marca GovHub. Isso é alteração de branding e
  se apoia na exceção dos 50 usuários — **reavaliar antes de abrir para a rede**.
- Nome limpo e substituição oficial de estáticos exigem `LICENSE_KEY` enterprise.

## Manutenção

A imagem está **pinada em `v0.10.2`**. O tema depende dos tokens desta build;
em `:main` um `pull` silencioso pode trocar o CSS por baixo. Ao subir de versão:
conferir `--color-gray-*`, `--tw-prose-*`, `#sidebar`, `#message-input-container`
e `button.waterfall`, que são os pontos de apoio do tema.
