// Aba Atualização: versão instalada × GitHub, changelog e o aviso na navegação.
// Serve igual no Windows e no Linux: repositório e comandos vêm do backend (sm/selfupdate.py).
import { $, esc, toast } from "../ui.js";
import { ic } from "../icons.js";

const POLL = 30 * 60 * 1000;
let app, root, data = null, armed = false, following = null;
const BUSY = ["starting", "running", "installing"];

const HEAD = {
  update: v => `Nova versão disponível: ${esc(v)}`,
  current: () => "Você está na versão mais recente",
  ahead: () => "Esta versão é mais nova que a do GitHub",
  off: () => "Verificação de versão desligada",
  unknown: () => "Ainda sem resposta do GitHub",
};

// O changelog vem da internet: escapa tudo antes e só cria links https.
function inline(s) {
  return esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/`(.+?)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https:\/\/[^\s)]+)\)/g, `<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>`)
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
}

function mount(el, a) {
  app = a; root = el;
  el.innerHTML = `<div class="grid">
    <article class="card span-2" data-status>Carregando…</article>
    <article class="card" data-steps></article>
    <article class="card span-all"><div class="card-head"><div class="card-title">O que mudou</div>
      <a class="btn btn-sm" data-log-link target="_blank" rel="noopener noreferrer">Ver no GitHub</a></div>
      <div data-log></div></article>
  </div>`;
  el.addEventListener("click", async e => {
    if (e.target.closest("[data-apply]")) return apply(e.target.closest("[data-apply]"));
    const b = e.target.closest("[data-check]");
    if (!b) return;
    b.disabled = true;
    try {
      data = await app.api("/api/panel-update/check", { method: "POST", body: {} });
      paint();
      if (data.error) toast(data.error, true);
      else toast(data.state === "update" ? "Há uma versão nova." : "Verificado: nenhuma versão nova.");
    } catch (err) { toast(err.message, true); b.disabled = false; }
  });
  poll();
  setInterval(poll, POLL);
}

// ---------- atualizar agora (só quando a pessoa clica) ----------
async function apply(btn) {
  if (!armed) {        // primeiro toque arma, o segundo confirma
    armed = true;
    btn.textContent = "Toque de novo para atualizar";
    setTimeout(() => { armed = false; paint(); }, 5000);
    return;
  }
  armed = false;
  btn.disabled = true;
  try {
    data = await app.api("/api/panel-update/apply", { method: "POST", body: {} });
    toast("Atualização iniciada. O painel fica fora do ar por cerca de 1 minuto e volta sozinho.");
    follow(data.installed);
  } catch (err) { toast(err.message, true); }
  paint();
}

/** Acompanha até a versão nova responder (o painel cai no meio e volta) e recarrega a página. */
function follow(from) {
  if (following) return;
  const started = Date.now();
  following = setInterval(async () => {
    try {
      const info = await app.api("/api/info");
      if (info.version !== from) { clearInterval(following); following = null; location.reload(); return; }
      data = await app.api("/api/panel-update");
      paint();
      if (!BUSY.includes(data.apply?.state)) { clearInterval(following); following = null; }
    } catch {
      paintOffline();                                 // reiniciando: a API não responde por alguns segundos
    }
    if (Date.now() - started > 6 * 60000) { clearInterval(following); following = null; }
  }, 3000);
}

function paintOffline() {
  const box = $("[data-apply-box]", root);
  if (box) box.innerHTML = `<div class="fan-alert info">${ic("arrow-clockwise")} Instalando a versão nova. O painel está reiniciando e esta página recarrega sozinha.</div>`;
}

function applyHtml() {
  const a = data.apply, recent = a?.ts && Date.now() / 1000 - a.ts < 86400;
  let out = "";
  if (a && BUSY.includes(a.state)) {
    out = `<div class="fan-alert info">${ic("arrow-clockwise")} ${esc(a.message || "Atualizando...")}</div>`;
    if (!following) follow(data.installed);
  } else if (a?.state === "error" && recent) {
    out = `<div class="fan-alert warning">A última atualização falhou: ${esc(a.message || "")}</div>`;
  } else if (a?.state === "done" && recent) {
    out = `<div class="fan-alert good">${ic("check")} ${esc(a.message || "Atualizado.")}</div>`;
  }
  if (!app.info?.features?.update_apply) return out;
  if (!data.can_apply) {
    return out + `<p class="note">Para atualizar por aqui, rode o <code>install.cmd</code> no PC uma vez: ele cria a tarefa que faz a atualização quando você pede.</p>`;
  }
  const busy = a && BUSY.includes(a.state);
  if (data.state === "update" && !busy) {
    out += `<button class="btn btn-primary" data-apply>${ic("download-simple")} Atualizar agora para ${esc(data.latest)}</button>
      <p class="note">Baixa do GitHub e instala no PC. O painel fica fora do ar por cerca de 1 minuto. Nada é instalado sem você pedir.</p>`;
  }
  return out;
}

async function poll() {
  try {
    data = await app.api("/api/panel-update");
    paint();
    if (data.checking) setTimeout(poll, 3000);   // o GitHub ainda vai responder
  } catch { /* conexão caiu; tenta no próximo ciclo */ }
}

function flagNav() {
  const on = data.state === "update";
  const tab = $('.tab[data-id="update"]'), more = $(".tab-more");
  if (tab && !!$(".tab-flag", tab) !== on) {
    if (on) tab.insertAdjacentHTML("beforeend", `<span class="tab-flag">Nova</span>`);
    else $(".tab-flag", tab).remove();
  }
  if (more) {
    more.classList.toggle("has-update", on);
    if (on) more.setAttribute("aria-label", "Mais, nova versão disponível");
    else more.removeAttribute("aria-label");
  }
}

function paint() {
  if (!data) return;
  flagNav();
  const when = data.checked ? new Date(data.checked * 1000).toLocaleString("pt-BR") : "Ainda não verificado";
  const chip = { update: ["warning", "Atualização disponível"], current: ["good", "Atualizado"] }[data.state];
  $("[data-status]", root).innerHTML = `
    <div class="card-head"><div class="card-title">Atualização do painel</div>${chip ? `<span class="chip ${chip[0]}">${chip[1]}</span>` : ""}</div>
    <p class="update-head">${HEAD[data.state]?.(data.latest) ?? ""}</p>
    ${data.error ? `<div class="fan-alert warning">${esc(data.error)}</div>` : ""}
    <dl class="kv">
      <dt>Instalada</dt><dd>${esc(data.installed)}</dd>
      <dt>No GitHub</dt><dd>${esc(data.latest || "—")}</dd>
      <dt>Última verificação</dt><dd>${esc(when)}</dd>
    </dl>
    <div data-apply-box>${applyHtml()}</div>
    <div class="update-actions">
      <button class="btn" data-check ${data.enabled ? "" : "disabled"}>${ic("arrow-clockwise")} Verificar agora</button>
      <a class="btn" href="${esc(data.repo_url)}" target="_blank" rel="noopener noreferrer">Abrir repositório</a>
    </div>
    ${data.enabled ? "" : `<p class="note">Para ligar, use <code>check = true</code> em <code>[update]</code> no config.toml.</p>`}`;

  $("[data-steps]", root).innerHTML = `
    <div class="card-head"><div class="card-title">Como atualizar</div></div>
    <pre class="update-cmd">${data.steps.map(esc).join("\n")}</pre>
    <p class="note">${esc(data.note)} <a href="${esc(data.guide_url)}" target="_blank" rel="noopener noreferrer">Guia completo</a></p>`;

  $("[data-log-link]", root).href = data.changelog_url;
  $("[data-log]", root).innerHTML = data.changelog.map(e => `
    <details class="release" ${e.new || e.installed ? "open" : ""}>
      <summary><span class="release-v">${esc(e.version)}</span>${e.date ? ` <span class="release-d">${esc(e.date.split("-").reverse().join("/"))}</span>` : ""}
        ${e.new ? `<span class="chip warning">Nova</span>` : e.installed ? `<span class="chip good">Instalada</span>` : ""}</summary>
      <ul>${e.items.map(i => `<li>${inline(i)}</li>`).join("")}</ul>
    </details>`).join("") || `<p class="muted">Changelog indisponível. Veja no GitHub.</p>`;
}

export default { id: "update", title: "Atualização", icon: ic("arrow-circle-up"), mount, show: poll };
