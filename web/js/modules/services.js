// Aba Serviços: serviços do Windows (e os por usuário), estado e consumo ao vivo, log de eventos e ações.
import { ic } from "../icons.js";
import { $, $$, bytes, esc, toast } from "../ui.js";
import { openInvestigate } from "./incidents.js";

let app, root, data = null, scope = "system", filter = "running", sort = "mem", q = "", timer = null, busy = false;
const SORTS = { mem: ["Memória", u => -(u.mem || 0)], cpu: ["CPU", u => -(u.cpu || 0)], name: ["Nome", u => u.unit],
  restarts: ["Reinícios", u => -(u.restarts || 0)], since: ["Mais recentes", u => -(u.since || 0)] };
const STATE = { running: ["good", "Rodando"], exited: ["", "Concluído"], dead: ["", "Parado"], failed: ["critical", "Falhou"],
  "start-pending": ["warning", "Iniciando"], "stop-pending": ["warning", "Parando"], "continue-pending": ["warning", "Retomando"],
  "pause-pending": ["warning", "Pausando"], paused: ["warning", "Pausado"] };
const START = { automatic: "Automático", manual: "Manual", disabled: "Desativado", Auto: "Automático", Manual: "Manual",
  Disabled: "Desativado", Boot: "Na inicialização", System: "Sistema" };

function ago(t) {
  if (!t) return "—";
  const s = Date.now() / 1000 - t;
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ${Math.floor(s % 3600 / 60)} min`;
  return `${Math.floor(s / 86400)} d`;
}
const stateChip = u => {
  const [c, l] = u.active === "failed" ? STATE.failed : STATE[u.sub] || ["", u.sub || u.active];
  return `<span class="chip ${c}">${esc(l)}</span>`;
};

function mount(el, a) {
  app = a; root = el;
  el.innerHTML = `
    <div class="toolbar">
      <input class="search" type="search" placeholder="Buscar serviço…" aria-label="Buscar serviço" data-q>
      <div class="seg" role="group" aria-label="Escopo" data-scope>
        <button data-v="system" aria-pressed="true">Windows</button><button data-v="user" aria-pressed="false">Por usuário</button></div>
      <div class="seg" role="group" aria-label="Filtro" data-filter>
        <button data-v="running" aria-pressed="true">Rodando</button><button data-v="failed" aria-pressed="false">Com falha</button>
        <button data-v="all" aria-pressed="false">Todos</button></div>
      <select class="search" data-sort aria-label="Ordenar">${Object.entries(SORTS).map(([k, [l]]) => `<option value="${k}">${l}</option>`).join("")}</select>
    </div>
    <div class="svc-sum" data-sum></div>
    <ul class="plist card svc-list" data-list><li class="empty">Carregando…</li></ul>
    <p class="note">CPU, memória e threads são as do processo do serviço. Vários serviços podem dividir o mesmo <code>svchost.exe</code>:
      nesse caso o consumo é do grupo inteiro (aparece "dividido com N"). "Por usuário" são as cópias de serviços que o Windows cria
      para cada conta logada (ex.: área de transferência, notificações).</p>`;
  $("[data-q]", el).addEventListener("input", e => { q = e.target.value.toLowerCase(); render(); });
  const seg = (sel, fn) => $(sel, el).addEventListener("click", e => {
    const b = e.target.closest("[data-v]"); if (!b) return;
    $$(`${sel} button`, el).forEach(x => x.setAttribute("aria-pressed", x === b));
    fn(b.dataset.v);
  });
  seg("[data-scope]", v => { scope = v; data = null; render(); load(); });
  seg("[data-filter]", v => { filter = v; render(); });
  $("[data-sort]", el).addEventListener("change", e => { sort = e.target.value; render(); });
  $("[data-list]", el).addEventListener("click", e => {
    const li = e.target.closest("[data-unit]");
    if (li) openUnit(li.dataset.unit);
  });
}

async function load() {
  if (busy) return;
  busy = true;
  try { data = await app.api(`/api/services?scope=${scope}`); render(); }
  catch (e) { $("[data-list]", root).innerHTML = `<li class="empty">${esc(e.message)}</li>`; }
  busy = false;
}

function render() {
  const list = $("[data-list]", root);
  if (!data) { list.innerHTML = `<li class="empty">Carregando…</li>`; $("[data-sum]", root).innerHTML = ""; return; }
  const c = data.counts;
  $("[data-sum]", root).innerHTML = `<span class="chip good">${c.running} rodando</span>
    ${c.failed ? `<span class="chip critical">${c.failed} com falha</span>` : `<span class="chip">nenhum com falha</span>`}
    <span class="chip">${c.total} instalados</span>`;
  let us = data.units.filter(u => filter === "all" || (filter === "running" ? u.sub === "running" : u.active === "failed"));
  if (q) us = us.filter(u => u.unit.toLowerCase().includes(q) || (u.description || "").toLowerCase().includes(q));
  const key = SORTS[sort][1];
  us.sort((a, b) => { const x = key(a), y = key(b); return x < y ? -1 : x > y ? 1 : 0; });
  if (!us.length) { list.innerHTML = `<li class="empty">${filter === "failed" ? "Nenhum serviço com falha." : "Nada encontrado."}</li>`; return; }
  list.innerHTML = us.slice(0, 400).map(u => `
    <li data-unit="${esc(u.unit)}" class="${u.active === "failed" ? "svc-failed" : ""}">
      <div style="min-width:0"><div class="pname">${esc(u.unit.replace(/\.service$/, ""))} ${stateChip(u)}</div>
        <div class="meta">${esc(u.description || "")}</div></div>
      <div class="vals"><b>${u.mem != null ? bytes(u.mem) : "—"}</b>
        <div class="meta">${u.cpu != null ? `CPU ${u.cpu.toFixed(1)}%` : ""}${u.tasks != null ? ` · ${u.tasks} threads` : ""}
          ${u.shared > 1 ? ` · dividido com ${u.shared - 1}` : ""}${u.exit ? ` · <span class="svc-warn">código ${u.exit}</span>` : ""}</div>
        <div class="meta">${u.since ? `há ${ago(u.since)}` : esc(START[u.enabled] || "")}</div></div>
    </li>`).join("");
}

// ---------- detalhe ----------
async function openUnit(unit) {
  const dlg = $("#sheet");
  dlg.innerHTML = `<h2>${esc(unit)}</h2><p class="muted">Carregando…</p>`;
  if (!dlg.open) dlg.showModal();
  let d;
  try { d = await app.api(`/api/services/${encodeURIComponent(unit)}?scope=${scope}`); }
  catch (e) { dlg.innerHTML = `<h2>${esc(unit)}</h2><p>${esc(e.message)}</p><div class="sheet-actions"><button class="btn full" data-close>Fechar</button></div>`; dlg.querySelector("[data-close]").onclick = () => dlg.close(); return; }
  const p = d.props || {};
  const u = data?.units.find(x => x.unit === unit) || {};
  const can = app.info?.features?.services_control;
  const running = p.State === "Running";
  const code = p.ExitCode === 1066 ? p.ServiceSpecificExitCode : p.ExitCode;
  const list = a => (a || []).length ? (a || []).map(esc).join(", ") : "—";
  dlg.innerHTML = `
    <h2>${esc(p.DisplayName || unit)}</h2>
    <div class="muted"><span class="mono">${esc(unit)}</span>${p.Description ? ` · ${esc(p.Description)}` : ""}</div>
    <dl class="kv" style="margin-top:10px">
      <dt>Estado</dt><dd>${stateChip(u.unit ? u : { active: running ? "active" : "inactive", sub: running ? "running" : "dead" })}</dd>
      <dt>Na inicialização</dt><dd>${esc(START[p.StartMode] || p.StartMode || "—")}${p.DelayedAutoStart ? " (atrasado)" : ""}</dd>
      ${d.since ? `<dt>Rodando desde</dt><dd>${new Date(d.since * 1000).toLocaleString("pt-BR")}</dd>` : ""}
      ${u.mem != null ? `<dt>Memória</dt><dd>${bytes(u.mem)}${u.shared > 1 ? ` (processo dividido com ${u.shared - 1} serviço${u.shared > 2 ? "s" : ""})` : ""}</dd>` : ""}
      ${u.cpu != null ? `<dt>CPU</dt><dd>${u.cpu.toFixed(1)}%</dd>` : ""}
      ${p.ProcessId ? `<dt>PID</dt><dd>${esc(p.ProcessId)}</dd>` : ""}
      <dt>Último código de saída</dt><dd>${code ? `<span class="svc-warn">${esc(code)}</span>` : "0 (normal)"}</dd>
      <dt>Conta</dt><dd>${esc(p.StartName || "—")}</dd>
      <dt>Processo</dt><dd>${/share/i.test(p.ServiceType || "") ? "compartilhado (svchost)" : /own/i.test(p.ServiceType || "") ? "próprio" : esc(p.ServiceType || "—")}</dd>
      <dt>Depende de</dt><dd>${list(p.DependsOn)}</dd>
      <dt>Dependem dele</dt><dd>${list(p.Dependents)}</dd>
    </dl>
    ${p.PathName ? `<div class="cmdline">${esc(p.PathName)}</div>` : ""}
    <details ${u.active === "failed" ? "open" : ""}><summary class="stat-label">Log de eventos do serviço</summary>
      <pre class="inc-pre">${esc((d.logs || []).map(m => `${new Date(m.ts * 1000).toLocaleString("pt-BR")} ${m.msg}`).join("\n") || "Nenhum evento recente do serviço.")}</pre></details>
    <div class="confirm-text" hidden></div>
    <div class="sheet-actions">
      ${can ? (running ? (d.protected ? "" : `<button class="btn" data-a="restart">Reiniciar</button><button class="btn btn-ghost-danger" data-a="stop">Parar</button>`)
        : p.StartMode !== "Disabled" ? `<button class="btn btn-primary" data-a="start">Iniciar</button>` : "") : ""}
      ${app.info?.features?.logs ? `<button class="btn" data-logs>Ver nos Logs</button>` : ""}
      ${app.info?.features?.incidents ? `<button class="btn" data-inv>Investigar</button>` : ""}
      <button class="btn full" data-close>Fechar</button>
    </div>
    ${d.protected && can ? `<p class="note">Serviço essencial do Windows (ou usado pelo painel): o painel não para nem reinicia.</p>` : ""}
    ${can && p.StartMode === "Disabled" ? `<p class="note">Desativado: para iniciar, mude o modo de início em services.msc no PC.</p>` : ""}`;
  dlg.querySelector("[data-close]").onclick = () => dlg.close();
  dlg.querySelector("[data-logs]")?.addEventListener("click", () => { dlg.close(); location.hash = `logs:grep=${encodeURIComponent(p.DisplayName || unit)}`; });
  dlg.querySelector("[data-inv]")?.addEventListener("click", () => { dlg.close(); openInvestigate(null, `Serviço ${unit}`); });
  let armed = null;
  dlg.querySelectorAll("[data-a]").forEach(b => b.addEventListener("click", async () => {
    const act = b.dataset.a;
    if (act === "stop" && armed !== act) {
      armed = act; b.textContent = "Toque de novo para confirmar";
      const c = dlg.querySelector(".confirm-text"); c.hidden = false;
      c.textContent = `Parar ${p.DisplayName || unit}? O que depende dele pode parar de funcionar.`;
      return;
    }
    b.disabled = true;
    try {
      await app.api(`/api/services/${encodeURIComponent(unit)}/${act}`, { method: "POST", body: { scope } });
      toast({ start: "Iniciado", stop: "Parado", restart: "Reiniciado" }[act] + `: ${p.DisplayName || unit}`);
      setTimeout(() => { load(); if (dlg.open) openUnit(unit); }, 800);
    } catch (e) { toast(e.message, true); b.disabled = false; }
  }));
}

export default {
  id: "services", title: "Serviços", icon: ic("package"),
  enabled: a => !!a.info?.features?.services,
  mount,
  show() { load(); timer = setInterval(() => { if (!document.hidden) load(); }, 3000); },
  hide() { clearInterval(timer); },
};
