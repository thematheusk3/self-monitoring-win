import { ic } from "../icons.js";
import { $, esc, statusChip } from "../ui.js";

let app, root;
const KIND = { temp: "Temperaturas", fan: "Ventoinhas", control: "Controle das ventoinhas", in: "Tensões",
  power: "Potência", curr: "Corrente", freq: "Frequência" };

function fmt(r) {
  const d = r.kind === "in" ? 3 : r.kind === "fan" || r.kind === "control" ? 0 : 1;
  return `${r.value.toFixed(d)} ${r.unit}`;
}

function mount(el, a) {
  app = a; root = el;
  el.innerHTML = `<div class="grid" data-grid></div>`;
  app.onSnapshot(update);
}

function update(snap) {
  if (!root.classList.contains("active")) return;
  const s = snap.data.sensors || {};
  const chips = s.chips || [];
  const grid = $("[data-grid]", root);
  if (!chips.length) {
    grid.innerHTML = `<div class="card span-all"><div class="card-head"><div class="card-title">Sensores indisponíveis</div></div>
      <p>O Windows não mostra temperaturas da CPU, ventoinhas e tensões da placa-mãe para programas comuns.
        O painel lê esses sensores do <b>LibreHardwareMonitor</b> (gratuito e open source).</p>
      <ol class="note" style="line-height:1.6">
        <li>Instale no PC: <code>winget install LibreHardwareMonitor.LibreHardwareMonitor</code> (ou baixe em github.com/LibreHardwareMonitor).</li>
        <li>Abra como administrador uma vez, feche e rode o <code>install.cmd</code>: ele liga o servidor web (<b>Options → Remote Web Server → Run</b>, porta 8085) de forma permanente.</li>
        <li>Para iniciar junto com o Windows: <b>Options → Run On Windows Startup</b> e <b>Start Minimized</b>.</li>
      </ol>
      <p class="muted">Endereço lido: <code>${esc(s.url || "")}</code>${s.error ? ` · ${esc(s.error)}` : ""}</p></div>`;
    return;
  }
  grid.innerHTML = chips.map(c => {
    const groups = {};
    for (const r of c.readings) (groups[r.kind] ||= []).push(r);
    const body = Object.entries(groups).map(([k, rs]) => `
      <div class="stat-label" style="margin-top:10px">${KIND[k] || k}</div>
      <dl class="kv">${rs.map(r => `<dt>${esc(r.label)}</dt><dd><span class="num">${fmt(r)}</span>${r.kind === "temp" ? statusChip(r.value, r.crit) : ""}</dd>`).join("")}</dl>`).join("");
    return `<article class="card"><div class="card-head"><div><div class="card-title">${esc(c.name)}</div>
      <div class="card-sub">${esc(c.driver)}</div></div></div>${body}</article>`;
  }).join("");
}

export default { id: "sensors", title: "Sensores", icon: ic("thermometer"), mount, show: () => app.snapshot && update(app.snapshot) };
