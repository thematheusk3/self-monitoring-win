// Aparência do painel: claro ou escuro e a cor da placa. As cores são as de máscara de solda de placas reais, com contatos
// dourados ou prateados; os tokens de cada uma estão em app.css. Fica salvo em cada aparelho, como o claro e escuro.
import { $, toast } from "./ui.js";

export const PALETTES = [
  { id: "verde", name: "Verde", deep: "#0d1f1a", mid: "#1c3b32", accent: "#e8be57" },
  { id: "azul", name: "Azul", deep: "#0c1a2b", mid: "#1b3552", accent: "#e8be57" },
  { id: "vermelha", name: "Vermelha", deep: "#230f12", mid: "#452022", accent: "#e8be57" },
  { id: "roxa", name: "Roxa", deep: "#1a1228", mid: "#33264d", accent: "#e8be57" },
  { id: "preta", name: "Preta", deep: "#121416", mid: "#272b2f", accent: "#d7dde2" },
  { id: "amarela", name: "Amarela", deep: "#211b0a", mid: "#413618", accent: "#ffd84d" },
];
const MODES = [["", "Automático"], ["dark", "Escuro"], ["light", "Claro"]];

const root = document.documentElement;
export const currentPalette = () => PALETTES.find(p => p.id === root.dataset.palette) || PALETTES[0];

function syncBrowserColor() {
  $('meta[name="theme-color"]').content = getComputedStyle(document.body).backgroundColor;
}

function setPalette(id) {
  if (id === PALETTES[0].id) { delete root.dataset.palette; localStorage.removeItem("palette"); }
  else { root.dataset.palette = id; localStorage.setItem("palette", id); }
}

function setMode(mode) {
  if (mode) { root.dataset.theme = mode; localStorage.setItem("theme", mode); }
  else { delete root.dataset.theme; localStorage.removeItem("theme"); }
}

export function openAppearance(app) {
  const sheet = $("#sheet");
  const render = () => {
    const cur = currentPalette().id, mode = root.dataset.theme || "";
    sheet.innerHTML = `<h2>Aparência</h2>
      <p class="confirm-text">Fica salvo neste aparelho.</p>
      <div class="seg appearance-modes">${MODES.map(([id, l]) => `<button data-appearance-mode="${id}" aria-pressed="${id === mode}">${l}</button>`).join("")}</div>
      <div class="swatches">${PALETTES.map(p => `<button class="swatch" data-appearance-palette="${p.id}" aria-pressed="${p.id === cur}" style="--mask:${p.mid};--pad:${p.accent}">
        <span class="swatch-board" aria-hidden="true"></span>${p.name}</button>`).join("")}</div>
      <div class="sheet-actions"><button class="btn full" data-appearance-close>Fechar</button></div>`;
  };
  render();
  sheet.onclick = e => {
    if (e.target.closest("[data-appearance-close]")) return sheet.close();
    const mode = e.target.closest("[data-appearance-mode]"), pal = e.target.closest("[data-appearance-palette]");
    if (!mode && !pal) return;
    if (mode) setMode(mode.dataset.appearanceMode);
    else setPalette(pal.dataset.appearancePalette);
    render();
    syncBrowserColor();
    if (app.snapshot) app.pushSnapshot(app.snapshot);   // redesenha os gráficos com as cores novas
  };
  sheet.addEventListener("close", () => { sheet.onclick = null; }, { once: true });
  sheet.showModal();
}

export function setupAppearance(app) {
  $("#theme-btn").addEventListener("click", () => {
    try { openAppearance(app); } catch (e) { toast(e.message, true); }
  });
  syncBrowserColor();
}
