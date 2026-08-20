# Estudo: identidade visual GovHub na interface do assistente

**Data:** 2026-08-20 · **Status:** estudo → **Opção A + B implementadas** (ver §7)
**Contexto:** hoje a interface é o **Open WebUI** (`docker-compose.yaml`, serviço
`webui`), consumindo o contrato OpenAI-compat exposto pelos wrappers do Hayhooks
(`Explorar` e `Indicador`, ADR-0014). O alvo estético é a identidade do GovHub
(skill `govhub-visual-identity`, materializada em `docs/graphrag-tais-overview.html`).

A pergunta é: **dá para vestir o Open WebUI de GovHub, em vez de escrever uma UI
própria?** Resposta curta: **dá, e mais do que eu esperava** — a build 0.10.2 já
tem dois ganchos oficiais de customização ativos e um sistema de *design tokens*
(Tailwind v4) que permite trocar a paleta inteira sem tocar em uma linha do
frontend. O limite não é técnico, é **de licença**.

---

## 1. O que foi verificado na instância (não é suposição)

Tudo abaixo foi inspecionado no container `open-webui` em execução:

| Achado | Onde | Consequência |
|---|---|---|
| Versão **0.10.2** | `/app/package.json` | referência para pinar a imagem |
| `/static/custom.css` existe, **vazio**, e é linkado no `<head>` | `/app/build/index.html` | `curl localhost:3000/static/custom.css` → **200**. Gancho de CSS livre. |
| `/static/loader.js` existe, **vazio**, `<script defer>` no `<head>` | idem | **200**. Gancho de JS livre (trocar textos, injetar DOM). |
| `/static` é servido de `/app/backend/open_webui/static` | `main.py:2567`, `env.py:240` (`STATIC_DIR`) | basta um *bind mount* de arquivo no compose |
| **Tailwind v4** com tokens `--color-gray-50…950` em `oklch()` no `:root` | `_app/immutable/assets/0.*.css` | a UI inteira é pintada com a escala `gray-*`; **sobrescrever a escala repinta o app todo** |
| **Inter já vem embarcada** (`@font-face { font-family: Inter }`) | mesma folha | a fonte da marca GovHub já está lá; só não é a primeira do stack (`html` começa com `-apple-system`) |
| `.font-primary` = **Archivo**, `.font-secondary` = InstrumentSerif | mesma folha | são os títulos/logotipo textual — trocáveis por Inter em 2 regras |
| Temas do app: `system` / `light` / `dark` / `oled-dark` / `her` | chunk do `+layout` | os `themes/rosepine*.css` da imagem **não** estão plugados no seletor desta build — não há "tema pluggável" oficial |
| `WEBUI_NAME` custom recebe **" (Open WebUI)" anexado à força** | `env.py:842-844` | rebranding do nome é bloqueado no código |
| Nome limpo + substituição de estáticos só via **`LICENSE_KEY`** | `utils/auth.py` (`get_license_data` → `override_static`) | white-label completo é feature paga |
| Envs de conteúdo disponíveis: `DEFAULT_LOCALE`, `DEFAULT_PROMPT_SUGGESTIONS`, `WEBUI_BANNERS`, `ENABLE_LOGIN_FORM` | `config.py:1617-2110` | personalização "de conteúdo" sem hack nenhum |
| A imagem está pinada em **`:main`** (`WEBUI_DOCKER_TAG:-main`) | `docker-compose.yaml` | **risco real**: `main` se move e pode quebrar o tema sem aviso |

---

## 2. A restrição que decide tudo: a cláusula de branding

O Open WebUI deixou de ser BSD puro. A licença atual proíbe **alterar, remover
ou ocultar o branding "Open WebUI"**, com três exceções:

1. deployments com **≤ 50 usuários** em qualquer janela de 30 dias;
2. permissão escrita do detentor dos direitos;
3. **licença enterprise**.

Tema custom **é permitido** — a discussão oficial do projeto confirma que CSS
próprio é aceitável *desde que o nome e o logotipo continuem visíveis e
inalterados*. O que a licença barra é o white-label (trocar logo/nome).

**Implicação prática para um projeto de governo:** enquanto for piloto/interno
(hoje é `WEBUI_AUTH=false`, uso restrito), a exceção dos 50 usuários cobre até
o rebranding completo. Se o assistente virar produto para a rede GovHub, a
conta muda: ou compra-se a licença enterprise, ou mantém-se o branding do Open
WebUI visível ao lado do GovHub (co-branding), ou migra-se para UI própria.
**Essa decisão deveria ser tomada antes de investir no tema**, porque ela define
qual das opções abaixo faz sentido.

---

## 3. Opções

### Opção A — `custom.css` + `loader.js` montados por volume  ⭐ recomendada

Nenhum fork, nenhum build. Dois arquivos e três linhas no compose.

**Cobre:** paleta inteira, tipografia, raios, sombras, botões, sidebar, bolhas
de mensagem, blocos de código, tabelas do Markdown (as respostas do `Indicador`
são tabela + gráfico — ganham muito), splash, favicon, logo.

**A jogada central** é não caçar classe por classe, e sim **redefinir a escala
`--color-gray-*`** com neutros levemente arroxeados. A UI inteira usa
`gray-50…300` para superfícies claras e `gray-700…950` para as escuras; trocando
a escala, os dois modos ficam GovHub de uma vez. O roxo-assinatura `#7A34F3`
entra **pontualmente** (botão de enviar, foco, item ativo da sidebar, `thead` das
tabelas) e o laranja `#F97316` fica reservado a um único CTA, como manda a skill.

**Esforço:** ~1 dia para chegar num resultado bom. **Risco de upgrade:** baixo
para os tokens (são API pública do Tailwind), médio para regras que dependem de
classe utilitária. Mitigação: **pinar `WEBUI_DOCKER_TAG=v0.10.2`** e revisar o
tema a cada bump — que é uma boa prática independentemente do tema.

**Cuidado de design:** `gray-*` é simultaneamente *neutro de texto* e *superfície*.
Puxar a escala para roxo saturado tinge legenda, borda e placeholder junto —
fica carnavalesco. O caminho é neutro quase-branco com um *hint* de roxo
(`oklch` com chroma ~0.01–0.02 no matiz do roxo) e o roxo forte só nos acentos.

### Opção B — camada de conteúdo (combina com A, custo quase zero)

Coisas que não são CSS e mesmo assim fazem a interface "ser" GovHub:

- **Workspace → Models**: dar aos modelos `Explorar` e `Indicador` nome de
  exibição, **descrição** e **ícone** (os SVGs de `govhub-visual-identity/references/icons/`
  — `name=database`, `name=charts`, `name=ia` caem como uma luva);
- `DEFAULT_PROMPT_SUGGESTIONS` com perguntas reais do catálogo (é o primeiro
  contato da pessoa com o produto e hoje está genérico);
- `WEBUI_BANNERS` com um aviso institucional GovHub;
- `DEFAULT_LOCALE=pt-BR`.

Zero risco de upgrade, zero fricção com a licença. **Faça isso mesmo que não
faça mais nada.**

### Opção C — fork do frontend SvelteKit + imagem própria

Controle total, inclusive layout e remoção de features. Custo: manter o fork
vivo, rebuildar a cada release upstream (build pesado, imagem de 6,3 GB), e o
frontend do Open WebUI muda rápido. **Só se A+B esbarrarem em layout**, não em cor.

### Opção D — UI própria usando o Open WebUI como *backend*

Frontend GovHub falando com a API do Open WebUI (`/api/chat/completions`,
`/api/v1/chats`, `/api/v1/memories`, `/api/v1/folders`, `/api/v1/knowledge`).
**Preserva exatamente aquilo que você não quer perder**, porque persistência de
conversa, memórias e compactação de contexto são todos **backend**. Custo alto
(é escrever um cliente de chat completo: streaming, markdown, código, mobile,
auth) e a relação com a cláusula de branding fica cinzenta — o backend continua
sendo Open WebUI. Reservar para quando houver requisito de **produto** (telas de
catálogo, explorador de grafo, galeria de relatórios) que o formato chat não comporte.

### Opção E — abandonar o Open WebUI, UI própria sobre o Hayhooks

O que se perde, concretamente — e é bem mais do que parece:

persistência e organização de conversas (chats, pastas, tags) · geração
automática de título · **memórias** (`routers/memories.py`) · **compactação de
contexto** (`utils/context_compaction.py`, com prompt de sumarização pronto) ·
upload de arquivos e base de conhecimento · RBAC, SSO/OAuth, SCIM · i18n ·
renderização de markdown/código/LaTeX/gráfico · responsivo · voz.

Recriar isso é o custo real, e é alto. **Não recomendo** enquanto o problema for
estético.

---

## 4. Sobre "o Open WebUI aloca bem a memória"

Preciso confirmar o que isso significa, porque muda a recomendação:

- **se for gestão de contexto/memória da conversa** — compactação de contexto,
  memórias, histórico: isso é **backend**, e está preservado em A, B, C e D.
  Só a Opção E perde;
- **se for consumo de RAM** — aí o Open WebUI 0.10.2 não é leve: imagem de
  **6,34 GB** e ~1,6 GB no disco descompactado, com Pyodide, WASM e sql.js
  embarcados. Nesse caso a premissa merece ser medida (`docker stats`) antes de
  virar critério de decisão.

---

## 5. Recomendação

1. **Decidir a questão da licença primeiro** (piloto ≤50 usuários vs. produto
   para a rede). É ela que autoriza ou não trocar logo e nome.
2. **Pinar a imagem** em `v0.10.2` — hoje está em `main`, alvo móvel.
3. Implementar **A + B**: `custom.css` com os tokens da skill `govhub-visual-identity`,
   `loader.js` só se precisar mexer em texto, ícones GovHub nos dois modelos,
   prompt suggestions reais, `pt-BR`.
4. Reavaliar **D** apenas quando surgir requisito de produto além do chat.
5. **E** fica descartada pelo motivo que você mesmo apontou: o que se perde não
   é a interface, é a infraestrutura de conversa por trás dela.

---

## 6. Esqueleto de implementação (Opção A)

`docker-compose.yaml`, serviço `webui`:

```yaml
  webui:
    image: ghcr.io/open-webui/open-webui:${WEBUI_DOCKER_TAG:-v0.10.2}
    volumes:
      - open-webui:/app/backend/data
      - ./webui/custom.css:/app/backend/open_webui/static/custom.css:ro
      - ./webui/loader.js:/app/backend/open_webui/static/loader.js:ro
      - ./webui/logo.png:/app/backend/open_webui/static/logo.png:ro
      - ./webui/favicon.png:/app/backend/open_webui/static/favicon.png:ro
      - ./webui/splash.png:/app/backend/open_webui/static/splash.png:ro
    environment:
      - DEFAULT_LOCALE=pt-BR
      - WEBUI_NAME=Assistente de Dados GovHub   # vira "... (Open WebUI)" sem licença
```

`webui/custom.css` — a espinha dorsal (valores de `oklch` a calibrar na tela):

```css
/* 1. Fonte da marca: a Inter JÁ está embarcada no Open WebUI. */
html, body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }
.font-primary { font-family: 'Inter', sans-serif; }   /* substitui Archivo */

/* 2. Neutros arroxeados — repinta o app inteiro (claro e escuro). */
:root, :host {
  --color-gray-50:  oklch(98.5% 0.006 300);
  --color-gray-100: oklch(96%   0.010 300);
  --color-gray-200: oklch(92%   0.014 300);
  --color-gray-300: oklch(85%   0.016 300);
  /* ...400-700 interpolando... */
  --color-gray-850: oklch(27%   0.030 300);
  --color-gray-900: oklch(21%   0.028 300);
  --color-gray-950: oklch(16%   0.024 300);
}

/* 3. Acentos da marca — pontuais, via seletor. */
:root {
  --govhub-purple: #7A34F3;
  --govhub-purple-hover: #7C3AAD;
  --govhub-orange: #F97316;
}
/* botão de enviar, foco, item ativo da sidebar, thead das tabelas do Markdown */
```

O bloco completo de tokens está em
`../../govhub-visual-identity/references/tokens.css`; as receitas de componente,
em `references/component-recipes.md`.

> Nota: `docs/graphrag-tais-overview.html` usa `#7c3aed`, não o `#7A34F3` oficial
> da skill. Para o tema da UI, a **skill é a fonte da verdade**.


---

## 7. O que foi implementado (2026-08-20)

Opções **A + B**, mais um ajuste no gráfico que o estudo não previa. Tudo em
[`webui/`](../webui/) — o README de lá é o manual de manutenção.

### Arquivos

| Arquivo | Papel |
|---|---|
| `webui/custom.css` | o tema (tokens, rail, markdown, tabelas, código, cards) |
| `webui/loader.js` | fixa tema claro e `pt-BR` antes do SvelteKit subir |
| `webui/assets/` | marca, favicon, splash e os ícones das personas |
| `docker-compose.yaml` | mounts + `WEBUI_NAME`, `DEFAULT_LOCALE`, imagem pinada |
| `src/rag/components/charts/renderers.py` | gráfico em roxo GovHub + Inter |

### O que o estudo não sabia e a implementação ensinou

1. **`/static` é uma cópia feita no boot.** `config.py` **apaga** o
   `STATIC_DIR` e o recopia de `/app/build/static` a cada start. Duas
   consequências: o mount tem de ser feito na **fonte** (`/app/build/static`,
   senão o boot loga `Read-only file system` em toda subida), e **editar o CSS
   exige `docker compose restart webui`**.
2. **Não precisa de `!important`.** Todo o CSS do Open WebUI está dentro de
   `@layer`; CSS sem layer vence layer sempre. Isso derrubou a preocupação com
   especificidade que o estudo levantava.
3. **O markdown inteiro sai de graça pelos `--tw-prose-*`** (Tailwind
   Typography) — tabela, código, link, citação, tudo. Não previsto no estudo e é
   o que mais rende, porque a resposta do `Indicador` é quase toda markdown.
4. **`--color-gray-*` confirmou-se a alavanca central**, como o estudo apostava.
5. **O modo escuro é irreconciliável** com um tema claro só por CSS: ele troca a
   escala inteira e ainda usa `dark:` com branco cravado. Daí o `loader.js`
   fixar claro — que é, aliás, o que o `overview.html` faz.
6. **Bug do upstream:** `POST /api/v1/models/model/update` estoura **500** se
   `access_grants` vier `null` — o endpoint revalida o próprio payload e o
   default do modelo não passa na validação. Mandar `[]`.
7. **CSS sem layer nem sempre basta.** Dois casos vencem o `custom.css`:
   componentes que injetam um `<style>` no fim do `<body>` (empatam em
   especificidade e ganham por ordem — é o caso do splash, resolvido com
   `html #splash-screen`) e atributos `style=` inline no elemento (só
   `!important` resolve).
8. **Em telas estreitas o `#sidebar` é outro elemento** — um drawer com árvore
   própria. Regra que dependia de herança de cor funcionava no desktop e deixava
   os rótulos **pretos sobre o roxo** no mobile.
9. **`/static/*` é servido sem `Cache-Control`**, só com `ETag`/`Last-Modified`.
   O navegador aplica cache heurístico e um F5 comum devolve a versão velha de CSS
   e de imagem. Depois de mexer no tema, recarregar forçado.
10. **Trocar o favicon é trocar QUATRO arquivos**, e o que vale na aba é o
    `favicon.ico` (o `index.html` o declara como `rel="shortcut icon"`).
    Substituir só `.png`/`.svg` deixa a aba com o ícone do Open WebUI. Os ícones
    também precisam ser gerados **quadrados** — o símbolo GovHub é 176×202 e sai
    achatado nas caixas `rounded-full`.
11. **O gráfico estava fora da identidade** e o estudo não tinha olhado: o
   `renderers.py` desenhava em azul-aço `#4E79A7` e verde-azulado `#0d8f80` (o
   comentário no código dizia ser "a identidade do projeto" — não era). Agora usa
   o roxo `#7c3aed`, a Inter que já vinha com o relatório e escala de eixo
   legível (`2,0 bi` em vez de `1e10`). Vale para o chat **e** para o `.docx`.

### Onde a marca aparece

Splash (tela cheia no gradiente da marca, com assinatura) · barra lateral ·
lockup na tela inicial + marca-d'água a 2,8% · **indicador de carregamento**
(o ponto que pulsava virou o símbolo GovHub — as respostas do Indicador levam de
30 s a 1 min, é onde a pessoa mais olha) · aba/favicon/PWA · avatares das
personas · gráfico.

### Decisões de design

- **A barra lateral é a navbar do GovHub, de pé.** O `overview.html` tem uma faixa
  horizontal em gradiente roxo com a logo branca; aqui o mesmo gradiente veste o
  rail vertical, que é o elemento equivalente na estrutura do Open WebUI.
- **Conversa ativa com marcador âmbar** — o mesmo acento que o overview usa no
  item de menu ativo, mas como marcador lateral e não como pílula preenchida:
  texto branco sobre `#f59e0b` daria contraste ruim em título de conversa.
- **Tabelas com a moldura no wrapper**, não na `<table>` — o container rolável
  cortava a sombra. É o mesmo arranjo do `.tw` do overview.

### Licença: co-branding, não white-label

- `WEBUI_NAME=Assistente de Dados GovHub`; o sufixo **" (Open WebUI)"** que o
  backend anexa **foi mantido** — o nome do produto segue visível na aba e no
  topo da barra lateral.
- O **ícone** passou a ser a marca GovHub. Isso é alteração de branding e se
  apoia na exceção dos **50 usuários**. É o ponto a reavaliar antes de abrir o
  assistente para a rede GovHub.

### Verificado ao vivo

Duas execuções reais no `Explorar` (panorama do catálogo + amostra de 5 linhas
com CSV) e duas no `Indicador` (barras por UF e série temporal, com SQL
executado, dicionário de dados, gráfico e link do `.docx`). Depois do
`docker compose restart webui`, o hash do arquivo servido bate com o do host e o
log sobe limpo.

### Pendências

- `DEFAULT_PROMPT_SUGGESTIONS` no compose só vale para instalação **nova**: numa
  existente o valor do banco vence. As sugestões atuais foram aplicadas por API e
  vivem no volume `open-webui`.
- A imagem foi **pinada em `v0.10.2`**, mas localmente isso é uma *tag* apontando
  para o `:main` que já estava na máquina (mesma build, `package.json` confirma).
  Num host novo, `docker compose pull webui` baixa a `v0.10.2` oficial.
