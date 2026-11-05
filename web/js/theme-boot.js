// Roda antes de a página aparecer, para o painel não piscar em verde ao abrir com outra cor.
(function () {
  var root = document.documentElement, theme = localStorage.getItem("theme"), palette = localStorage.getItem("palette");
  if (theme) root.dataset.theme = theme;
  if (palette) root.dataset.palette = palette;
})();
