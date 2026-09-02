/* ============================================================================
   GovHub — ajustes de boot do Open WebUI 0.11.0
   ----------------------------------------------------------------------------
   Servido em /static/loader.js (gancho oficial, <script defer> no <head>).
   Roda depois do script inline de tema e ANTES do bootstrap do SvelteKit,
   que é a janela exata para corrigir o tema sem o app já ter lido o valor.

   Duas coisas moram aqui:
     1. tema claro fixo + locale pt-BR (abaixo);
     2. a INTRO da marca — a tela roxa com o símbolo quicando, a mensagem de
        boas-vindas e o botão "Começar" (ver o bloco no fim do arquivo).
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
    if (meta) meta.setAttribute('content', '#7c3aed'); // roxo GovHub na barra do mobile

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
   desabilitado como "Preparando…". Ou seja, o quique não é enfeite em cima de
   uma espera falsa — ele cobre a espera real, e o botão só arma quando clicar
   leva mesmo para um app pronto.

   DEGRADAÇÃO: se qualquer coisa aqui falhar, o `catch` deixa o boot seguir e o
   usuário cai no splash nativo (que o custom.css já tematiza). A intro só
   esconde o splash nativo DEPOIS de conseguir se montar — a classe
   `gh-intro-on` no <html> é o interruptor, e ela é ligada junto com o append.
   ========================================================================= */
(function () {
  var MS_REDE = 20000;      // teto de espera pelo app antes de armar o botão assim mesmo
  var MS_SOCORRO = 16000;   // teto absoluto do percurso, se o animationend nunca vier

  var MENSAGEM =
    'Olá, eu sou a chatbot Tais! Vou te acompanhar nessa jornada do datathon: ' +
    'te ajudo a explorar as bases de dados, tabelas e informações disponíveis, ' +
    'além de montar indicadores, relatórios e gráficos a partir deles. ' +
    'É só me contar o que você precisa. Vamos começar?';

  function pouparMovimento() {
    try {
      return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch (e) {
      return false;
    }
  }

  function montar() {
    if (document.getElementById('gh-intro')) return;

    var intro = document.createElement('div');
    intro.id = 'gh-intro';
    intro.setAttribute('role', 'dialog');
    intro.setAttribute('aria-modal', 'true');
    intro.setAttribute('aria-label', 'TAIS GovHub — Assistente de Dados');
    intro.innerHTML = [
      '<div class="gh-intro-halo"></div>',
      '<div class="gh-intro-chao"></div>',
      '<div class="gh-intro-x">',
      '  <div class="gh-intro-sombra"></div>',
      '  <div class="gh-intro-y">',
      '    <img class="gh-intro-marca" src="/static/govhub-mark-light.svg" alt="" aria-hidden="true">',
      '  </div>',
      '</div>',
      '<div class="gh-intro-final">',
      '  <div class="gh-intro-vaga" aria-hidden="true"></div>',
      '  <p class="gh-intro-assinatura">Assistente de Dados TAIS</p>',
      '  <p class="gh-intro-msg"></p>',
      '  <button type="button" class="gh-intro-cta" disabled>Preparando…</button>',
      '</div>'
    ].join('');

    // textContent, não innerHTML: a mensagem é texto, e assim ela nunca vira
    // marcação por descuido de quem for editá-la depois.
    intro.querySelector('.gh-intro-msg').textContent = MENSAGEM;

    document.body.appendChild(intro);
    document.documentElement.classList.add('gh-intro-on');

    var botao = intro.querySelector('.gh-intro-cta');
    var eixoX = intro.querySelector('.gh-intro-x');
    var eixoY = intro.querySelector('.gh-intro-y');
    var marca = intro.querySelector('.gh-intro-marca');
    var vaga = intro.querySelector('.gh-intro-vaga');

    // ---- fim do percurso: o símbolo assenta na vaga do bloco final ---------
    // O pouso é CALCULADO, não medido no símbolo em movimento. O CSS garante que
    // as keyframes terminam com o símbolo centrado na viewport, então o ponto de
    // partida é (innerWidth/2, innerHeight/2) e o tamanho de partida é a largura
    // COMPUTADA (que ignora os transforms do quique). Medir a caixa animada daria
    // um valor diferente a cada quadro em que a função caísse.
    // O destino continua sendo medido, porque depende do texto: a mensagem quebra
    // em mais ou menos linhas conforme a tela e a vaga desce junto.
    var assentado = false;
    function assentar() {
      if (assentado) return;
      var alvo = vaga.getBoundingClientRect();
      var base = parseFloat(window.getComputedStyle(marca).width);
      // ⚠️ ABA EM SEGUNDO PLANO NÃO TEM LAYOUT: rects e innerWidth vêm ZERADOS.
      // Sem esta guarda o pouso é calculado sobre lixo e o símbolo é atirado
      // para milhares de pixels fora da tela — quem abriu o app numa aba de
      // fundo (Cmd+clique, sessão restaurada) voltava para a mensagem SEM a
      // marca. requestAnimationFrame não roda em aba escondida, então este
      // caminho espera sozinho até a aba aparecer.
      if (!alvo.width || !base || !window.innerWidth) {
        window.requestAnimationFrame(assentar);
        return;
      }
      assentado = true;
      var escala = alvo.width / base;
      var dx = alvo.left + alvo.width / 2 - window.innerWidth / 2;
      var dy = alvo.top + alvo.height / 2 - window.innerHeight / 2;
      // As animações precisam MORRER antes: animação com fill:forwards ganha
      // de style inline, então o transform novo seria simplesmente ignorado.
      // Como elas terminam em translate(0,0)/scale(1), matá-las não salta.
      eixoX.style.animation = 'none';
      eixoY.style.animation = 'none';
      marca.style.animation = 'none';
      void eixoY.offsetWidth; // reflow: sem isto o browser agrupa e não transiciona
      // translate + scale VÃO JUNTOS no eixoY, e não separados no <img>: o
      // <img> é achatado no impacto com origem no pé (transform-origin 50%
      // 100%), então crescer por ele empurraria o símbolo para cima e o pouso
      // sairia do lugar calculado. O eixoY é uma caixa 0x0 no centro exato do
      // símbolo — escalar por ali mantém o centro parado.
      eixoY.style.transform =
        'translate(' + dx + 'px,' + dy + 'px) scale(' + escala + ')';
      intro.classList.add('gh-fase-final');
    }

    // ---- saída -------------------------------------------------------------
    var saindo = false;
    function sair() {
      if (saindo) return;
      saindo = true;
      intro.classList.add('gh-fase-saida');
      document.documentElement.classList.remove('gh-intro-on');
      window.setTimeout(function () {
        if (intro.parentNode) intro.parentNode.removeChild(intro);
      }, 520);
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
      intro.classList.add('gh-pronto');
      if (intro.classList.contains('gh-fase-final')) {
        try { botao.focus({ preventScroll: true }); } catch (e) { botao.focus(); }
      }
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

    // ---- linha do tempo ----------------------------------------------------
    if (pouparMovimento()) {
      // Quem pediu menos movimento não leva quique nenhum: vai direto ao fim.
      intro.classList.add('gh-sem-movimento');
      assentar();
    } else {
      intro.classList.add('gh-fase-salto');
      // ⚠️ Quem manda no fim do percurso é o `animationend`, NÃO um setTimeout.
      // O navegador congela animação de aba escondida, mas o setTimeout segue
      // correndo: cronometrar por relógio de parede fazia o pouso acontecer no
      // meio de um quique que nem tinha começado. O evento vem do relógio da
      // própria animação, então aba de fundo apenas adia — e quem chega vê o
      // percurso inteiro, do começo.
      eixoX.addEventListener('animationend', function (ev) {
        if (ev.target === eixoX) assentar();
      });
      // Rede de segurança para o caso de o evento nunca vir (animação desligada
      // por extensão, motor sem suporte). Folgada de propósito: apertada, ela
      // roubaria o percurso de quem está numa aba de fundo.
      window.setTimeout(assentar, MS_SOCORRO);
    }
  }

  try {
    if (document.body) montar();
    else document.addEventListener('DOMContentLoaded', montar);
  } catch (e) {
    // Sem intro, o splash nativo tematizado assume — a classe gh-intro-on só é
    // ligada em caso de sucesso, então nada fica escondido por engano.
  }
})();
