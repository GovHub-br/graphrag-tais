/* ============================================================================
   GovHub — ajustes de boot do Open WebUI 0.10.2
   ----------------------------------------------------------------------------
   Servido em /static/loader.js (gancho oficial, <script defer> no <head>).
   Roda depois do script inline de tema e ANTES do bootstrap do SvelteKit,
   que é a janela exata para corrigir o tema sem o app já ter lido o valor.

   A identidade do GovHub é assumidamente CLARA (o overview.html chega a forçar
   color-scheme:light). O tema escuro do Open WebUI não é apenas outra cor: ele
   troca a escala de cinza inteira e usa utilitários dark: com branco cravado,
   que o custom.css não tem como reconciliar. Então fixamos claro.
   ========================================================================= */
(function () {
  try {
    // 1. Tema claro, sempre.
    if (localStorage.theme !== 'light') localStorage.theme = 'light';
    var root = document.documentElement;
    root.classList.remove('dark', 'oled-dark', 'her');
    root.classList.add('light');

    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', '#7c3aed'); // roxo GovHub na barra do mobile

    // 2. Português como idioma inicial (só na primeira visita — respeita quem trocar).
    if (!localStorage.locale) localStorage.locale = 'pt-BR';

    // 3. O splash é montado pelo script inline com src cravado. Em tema claro
    //    ele pega splash.png (logo roxa); como o custom.css pinta o splash com
    //    o gradiente da marca, a logo tem de ser a versão clara.
    document.addEventListener('DOMContentLoaded', function () {
      var logo = document.getElementById('logo');
      if (logo) logo.src = '/static/splash-dark.png';
    });
  } catch (e) {
    /* nunca derrubar o boot da UI por causa do tema */
  }
})();
