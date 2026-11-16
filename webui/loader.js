/* ============================================================================
   GovHub — ajustes de boot do Open WebUI 0.11.0
   ----------------------------------------------------------------------------
   Servido em /static/loader.js (gancho oficial, <script defer> no <head>).
   Roda depois do script inline de tema e ANTES do bootstrap do SvelteKit,
   que é a janela exata para corrigir o tema sem o app já ter lido o valor.

   Três coisas moram aqui:
     1. tema claro fixo + locale pt-BR (abaixo);
     2. a INTRO da marca — a tela navy com a logomarca, a mensagem de
        boas-vindas e o botão "Começar", uma vez por sessão;
     3. a resposta nova aparece pelo COMEÇO, não pelo fim (último bloco).
   ========================================================================= */
(function () {
  try {
    // 1. Tema claro, sempre.
    //    A identidade do GovHub é assumidamente CLARA (o overview.html chega a
    //    forçar color-scheme:light). O tema escuro do Open WebUI não é apenas
    //    outra cor: troca a escala de cinza inteira e usa utilitários dark: com
    //    branco cravado, que o custom.css não tem como reconciliar.
    if (localStorage.theme !== 'light') localStorage.theme = 'light';
    var root = document.documentElement;
    root.classList.remove('dark', 'oled-dark', 'her');
    root.classList.add('light');

    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', '#ffffff'); // barra do mobile = a superfície do app

    // 2. Português como idioma inicial (só na primeira visita — respeita quem trocar).
    if (!localStorage.locale) localStorage.locale = 'pt-BR';

    // 3. O splash NATIVO é montado pelo script inline com src cravado. Ele fica
    //    embaixo da nossa intro e só aparece se ela não subir (ver abaixo), mas
    //    quando aparece tem de estar certo: em tema claro o script pega
    //    splash.png (logo roxa), e o custom.css pinta o fundo com o gradiente
    //    da marca — então a logo tem de ser a versão clara.
    document.addEventListener('DOMContentLoaded', function () {
      var logo = document.getElementById('logo');
      if (logo) logo.src = '/static/splash-dark.png';
    });
  } catch (e) {
    /* nunca derrubar o boot da UI por causa do tema */
  }
})();

/* ============================================================================
   INTRO DA MARCA
   ----------------------------------------------------------------------------
   POR QUE NÃO É O SPLASH NATIVO. O `#splash-screen` do Open WebUI é removido
   pelo próprio app (`+layout.svelte`: `getElementById('splash-screen')?.remove()`)
   assim que o carregamento termina — normalmente meio segundo. Não há gancho
   para segurá-lo, e qualquer tentativa de prendê-lo briga com o ciclo de vida
   do app. Então a intro é um elemento NOSSO, por cima, com tempo próprio: quem
   decide quando ela sai é o clique em "Começar", não o boot.

   O que o carregamento controla é só o ESTADO DO BOTÃO: enquanto o app não
   terminou de subir (o `#splash-screen` nativo ainda está no DOM), o botão fica
   desabilitado como "Preparando…". O botão só arma quando clicar leva mesmo
   para um app pronto.

   A CADA CARREGAMENTO DE PÁGINA (decisão de produto, 16/11). Toda vez que a
   página é carregada — F5, link de conversa aberto, aba nova — a intro sobe de
   novo. Navegar DENTRO do app (trocar de conversa, novo chat) não recarrega a
   página e não a mostra. Entre 13/11 e 16/11 ela aparecia uma vez por sessão
   (marca no `sessionStorage`); voltou a ser por carregamento porque a tela é a
   apresentação do produto e o pedido foi vê-la ao atualizar. Se um dia o
   pedágio pesar, o gancho é uma guarda em volta da chamada de `montar()`.

   O movimento mora todo no custom.css (seção 10.1); aqui não há relógio de
   animação nenhum.

   DEGRADAÇÃO: se qualquer coisa aqui falhar, o `catch` deixa o boot seguir e o
   usuário cai no splash nativo (que o custom.css já tematiza). A intro só
   esconde o splash nativo DEPOIS de conseguir se montar — a classe
   `gh-intro-on` no <html> é o interruptor, e ela é ligada junto com o append.
   ========================================================================= */
(function () {
  var MS_REDE = 20000;      // teto de espera pelo app antes de armar o botão assim mesmo

  // Texto de produto de uso geral (15/11): o de antes falava com o datathon.
  // As três garantias são fatos do sistema, não promessa — a resposta cita a
  // tabela de origem, o SQL roda em modo somente leitura (ADR-0016/0019) e o
  // .docx é gerado do que a pessoa viu. Se uma delas deixar de ser verdade,
  // sai daqui.
  var TITULO = 'Dados públicos, respostas verificáveis.';
  var MENSAGEM =
    'Pergunte em português. O assistente localiza as bases no catálogo, ' +
    'consulta os dados e devolve a resposta com fonte, método de cálculo e ' +
    'gráficos, pronta para virar relatório.';
  var GARANTIAS = [
    'Fonte citada em cada resposta',
    'Consulta somente leitura, sem alterar dados',
    'Relatório editável em Word'
  ];

  function montar() {
    if (document.getElementById('gh-intro')) return;

    var intro = document.createElement('div');
    intro.id = 'gh-intro';
    intro.setAttribute('role', 'dialog');
    intro.setAttribute('aria-modal', 'true');
    intro.setAttribute('aria-label', 'TAIS GovHub — Assistente de Dados');
    intro.innerHTML = [
      '<div class="gh-intro-forma gh-intro-semi" aria-hidden="true"></div>',
      '<div class="gh-intro-forma gh-intro-anel" aria-hidden="true"></div>',
      '<div class="gh-intro-forma gh-intro-pilula" aria-hidden="true"></div>',
      '<div class="gh-intro-forma gh-intro-quarto" aria-hidden="true"></div>',
      '<div class="gh-intro-forma gh-intro-ponto" aria-hidden="true"></div>',
      '<div class="gh-intro-final">',
      '  <img class="gh-intro-logo" src="/static/govhub-logomarca-white.svg" alt="Gov Hub">',
      '  <p class="gh-intro-assinatura">Assistente de Dados TAIS</p>',
      '  <h1 class="gh-intro-titulo"></h1>',
      '  <p class="gh-intro-msg"></p>',
      '  <ul class="gh-intro-garantias"></ul>',
      '  <button type="button" class="gh-intro-cta" disabled>Preparando…</button>',
      '</div>'
    ].join('');

    // textContent, não innerHTML: os textos são texto, e assim nunca viram
    // marcação por descuido de quem for editá-los depois.
    intro.querySelector('.gh-intro-titulo').textContent = TITULO;
    intro.querySelector('.gh-intro-msg').textContent = MENSAGEM;
    var lista = intro.querySelector('.gh-intro-garantias');
    GARANTIAS.forEach(function (texto) {
      var item = document.createElement('li');
      item.textContent = texto;
      lista.appendChild(item);
    });

    document.body.appendChild(intro);
    document.documentElement.classList.add('gh-intro-on');

    var botao = intro.querySelector('.gh-intro-cta');

    // ---- foco preso dentro da intro ---------------------------------------
    // 🔴 ISTO NÃO É SÓ ACESSIBILIDADE. `role="dialog"` + `aria-modal` apenas
    // DECLARAM que a tela é modal; nada nesses atributos mexe na ordem de
    // tabulação nem impede o app de roubar o foco. E o Open WebUI faz
    // exatamente isso: ao hidratar, ele dá foco ao `#chat-input` — que está
    // DEBAIXO da intro. Medido antes desta guarda: digitar com a intro aberta
    // enchia a caixa de mensagem invisível, e um Tab caía nos ~28 controles do
    // app escondido. Quem clicasse "Começar" encontrava texto que não quis
    // escrever esperando na caixa.
    //
    // A guarda é por `focusin` em captura, e não uma armadilha de Tab: assim
    // ela cobre os três caminhos de uma vez — Tab, clique e `focus()`
    // programático do app. Não entra em laço, porque o foco que ela devolve cai
    // dentro da intro e a condição passa a ser falsa.
    var saindo = false;
    intro.tabIndex = -1;   // sem isto o container não recebe foco e não há para onde devolver

    function focar(elemento) {
      try { elemento.focus({ preventScroll: true }); } catch (e) { elemento.focus(); }
    }
    function focoParaDentro() {
      focar(botao.disabled ? intro : botao);
    }
    function guardaDeFoco(ev) {
      if (!saindo && !intro.contains(ev.target)) focoParaDentro();
    }
    document.addEventListener('focusin', guardaDeFoco, true);
    focoParaDentro();

    // ---- saída -------------------------------------------------------------
    function sair() {
      if (saindo || botao.disabled) return;
      saindo = true;
      document.removeEventListener('focusin', guardaDeFoco, true);
      intro.classList.add('gh-fase-saida');
      document.documentElement.classList.remove('gh-intro-on');
      // Devolve ao app o foco que a guarda tirou dele. `#chat-input` é o que o
      // Open WebUI foca sozinho no boot, então isto restaura o estado que
      // existiria sem a intro — sem ele, sair deixaria o foco no <body> e quem
      // usa teclado teria de tabular até a caixa. Defensivo: se um dia o id
      // mudar, o pior caso é o foco ficar no <body>, como já ficava.
      var entrada = document.getElementById('chat-input');
      if (entrada) focar(entrada);
      window.setTimeout(function () {
        if (intro.parentNode) intro.parentNode.removeChild(intro);
      }, 480);
    }
    botao.addEventListener('click', sair);

    // ---- o botão só arma quando o app está de pé ---------------------------
    // O sinal é o sumiço do #splash-screen nativo: é a última coisa que o
    // +layout.svelte faz quando termina de carregar.
    var armado = false;
    function armar() {
      if (armado) return;
      armado = true;
      botao.disabled = false;
      botao.textContent = 'Começar';
      focoParaDentro();
    }
    var vigia = window.setInterval(function () {
      if (!document.getElementById('splash-screen')) {
        window.clearInterval(vigia);
        armar();
      }
    }, 120);
    // Teto: app travado, rota de erro, o que for — ninguém fica preso aqui.
    window.setTimeout(function () {
      window.clearInterval(vigia);
      armar();
    }, MS_REDE);
  }

  try {
    if (document.body) montar();
    else document.addEventListener('DOMContentLoaded', montar);
  } catch (e) {
    // Sem intro, o splash nativo tematizado assume — a classe gh-intro-on só é
    // ligada em caso de sucesso, então nada fica escondido por engano.
  }
})();


/* ============================================================================
   RESPOSTA PELO COMEÇO
   ----------------------------------------------------------------------------
   O Open WebUI segue o fim da conversa enquanto a resposta chega
   (`autoScroll`). Num chat de texto curto isso é o certo; aqui as respostas
   são longas e começam pelo que importa (a frase com o número, os cartões) —
   no celular a pessoa clicava em "Números para decisão" e caía nos botões do
   FIM, tendo de subir para achar o que pediu.

   Então: quando a pessoa ENVIA (botão de caminho, botão de enviar ou Enter), a
   mensagem dela fica presa no topo da tela enquanto a resposta chega embaixo.
   Presa até: a resposta TERMINAR (a barra de ações dela aparece — o Open WebUI
   só a desenha com a mensagem pronta) e o DOM ficar MS_QUIETO sem mudar (os
   botões de caminho chegam logo depois); a pessoa rolar por conta própria
   (toque ou roda — ela manda); ou o teto MS_LIMITE. "Só silêncio" não basta:
   enquanto o LLM pensa (10 s numa pergunta nova) a tela fica parada, e soltar
   ali devolvia a resposta ao rolar-até-o-fim. Fixar a cada mudança — e não uma vez só — é o que vence o
   `scrollToBottom` do app, que roda a cada pedaço da resposta.

   DEGRADAÇÃO: qualquer seletor que mudar numa versão nova do Open WebUI só
   desliga isto; o comportamento volta a ser o nativo.
   ========================================================================= */
(function () {
  var MS_LIMITE = 180000;  // pergunta lenta do Indicador passa de 1 min
  var MS_QUIETO = 2500;    // sem mudança por esse tempo depois da resposta = terminou
  var ENVIO = 'button[aria-label^="Acompanhamento"], button[aria-label^="Follow up"], ' +
              '#send-message-button';
  // Botões da barra de ações de uma resposta pronta (pt-BR e inglês).
  var PRONTA = /^(Boa Resposta|Resposta Ruim|Good Response|Bad Response|Regenerar|Regenerate)$/;

  var armado = null;   // { antes: ids das mensagens do usuário no clique, t0 }
  var ancora = null;   // id da mensagem do usuário que fica no topo
  var observador = null;
  var quieto = null;
  var quadro = 0;

  function caixa() { return document.getElementById('messages-container'); }

  function idsDoUsuario() {
    var ids = {};
    var c = caixa();
    if (!c) return ids;
    var lista = c.querySelectorAll('.user-message');
    for (var i = 0; i < lista.length; i++) ids[lista[i].id] = true;
    return ids;
  }

  function terminou() {
    // As mensagens não são irmãs no DOM (cada uma tem seu invólucro): a
    // resposta é a próxima `message-…` em ordem de documento.
    var c = caixa();
    if (!c || !ancora) return false;
    var lista = c.querySelectorAll('[id^="message-"]');
    var depois = false;
    for (var i = 0; i < lista.length; i++) {
      if (lista[i].id === ancora) { depois = true; continue; }
      if (!depois) continue;
      var botoes = lista[i].querySelectorAll('button[aria-label]');
      for (var j = 0; j < botoes.length; j++) {
        if (PRONTA.test(botoes[j].getAttribute('aria-label'))) return true;
      }
    }
    return false;
  }

  function soltar() {
    if (observador) observador.disconnect();
    observador = null;
    ancora = null;
    armado = null;
    window.clearTimeout(quieto);
    window.cancelAnimationFrame(quadro);
  }

  // Pelo ID, não pelo elemento: num chat novo o app remonta a conversa ao
  // trocar de rota, e o elemento do clique deixa de ser o que está na tela.
  function elAncora() { return ancora ? document.getElementById(ancora) : null; }

  function fixar() {
    quadro = 0;
    var el = elAncora();
    // `scroll-margin-top` da própria mensagem (3rem na 0.11.0) desconta o topo fixo.
    if (el) el.scrollIntoView({ block: 'start' });
  }

  function aoMudar() {
    if (!armado) return;
    if (Date.now() - armado.t0 > MS_LIMITE) return soltar();
    if (!ancora) {
      var c = caixa();
      var lista = c ? c.querySelectorAll('.user-message') : [];
      var ultima = lista.length ? lista[lista.length - 1] : null;
      if (!ultima || !ultima.id || armado.antes[ultima.id]) return;  // ainda não entrou
      ancora = ultima.id;
    }
    // Antes do próximo quadro: depois do scrollToBottom do app, antes de pintar.
    if (!quadro) quadro = window.requestAnimationFrame(fixar);
    window.clearTimeout(quieto);
    if (!terminou()) return;
    quieto = window.setTimeout(function () {
      fixar();
      soltar();
    }, MS_QUIETO);
  }

  function armar() {
    if (typeof MutationObserver === 'undefined' || !document.body) return;
    soltar();
    armado = { antes: idsDoUsuario(), t0: Date.now() };
    // O BODY, não a caixa de mensagens: na primeira pergunta de um chat novo a
    // caixa ainda não existe no clique — o app troca de rota (/ → /c/<id>) e
    // só então monta a conversa.
    observador = new MutationObserver(aoMudar);
    observador.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  try {
    document.addEventListener('click', function (ev) {
      var alvo = ev.target && ev.target.closest ? ev.target.closest(ENVIO) : null;
      if (alvo) armar();
    }, true);
    document.addEventListener('keydown', function (ev) {
      var t = ev.target;
      if (ev.key === 'Enter' && !ev.shiftKey && t && t.closest && t.closest('#chat-input')) armar();
    }, true);
    // A pessoa rolou: dela em diante quem manda é ela.
    ['wheel', 'touchmove'].forEach(function (tipo) {
      document.addEventListener(tipo, function () { if (ancora) soltar(); },
                                { capture: true, passive: true });
    });
  } catch (e) {
    /* sem isto, vale o rolar-até-o-fim nativo */
  }
})();
