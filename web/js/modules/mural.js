// Aba Mural: abre a tela cheia e guarda as preferências (cidade, modo, modo jogo, capas). O palco fica em ../mural-stage.js.
// Abertura direta (ex.: tablet fixo): endereço do painel + #mural:abrir
import { $, $$, esc, toast } from "../ui.js";
import { ic } from "../icons.js";
import { close as closeStage, open as openStage } from "../mural-stage.js";

const MODE_HELP = {
  auto: "Mostra o jogo se houver um aberto, senão a música que está tocando, senão o Início.",
  home: "Sempre relógio, data e clima.",
  game: "O jogo aberto, com a arte dele de fundo. Sem jogo, mostra o Início.",
  music: "A música atual, com a capa de fundo. Sem música, mostra o Início.",
};
const MODES = [["auto", "Automático"], ["home", "Início"], ["game", "Jogo"], ["music", "Música"]];
const GAME_OPTS = [["fps", "FPS"], ["low1", "1% low"], ["frametime", "Frametime"], ["cpu", "Uso da CPU"], ["gpu", "Uso da GPU"],
  ["temps", "Temperaturas"], ["vram", "VRAM"], ["ram", "Memória RAM"], ["session", "Tempo de jogo"], ["music", "Player de música"]];
const ART_KINDS = [["hero", "Fundo da biblioteca"], ["cover", "Capa vertical"], ["header", "Banner da loja"]];
const MAX_SIDE = 1920, MAX_DATA = 1_150_000;      // cabe no limite de 1 MB do servidor depois de decodificado

let app, root, conf = null, games = null, q = "", searchTimer = null;

const artUrl = (g, kind) => `/api/mural/art/${encodeURIComponent(g.key)}${kind ? `?kind=${kind}` : `?v=${g.v}`}`;

function mount(el, a) {
  app = a; root = el;
  el.innerHTML = `<div class="grid">
    <article class="card span-2 mural-open-card">
      <div class="card-head"><div class="card-title">Mural</div></div>
      <p class="note">Uma tela cheia para deixar aberta num monitor, tablet ou celular: relógio, clima, o jogo que está aberto e a música que está tocando.</p>
      <button class="btn btn-primary" data-open>${ic("frame-corners")} Abrir o Mural</button>
      <p class="note">Para abrir direto em outro aparelho, salve este endereço: <code>${esc(location.origin)}/#mural:abrir</code></p>
    </article>
    <article class="card" data-city></article>
    <article class="card">
      <div class="card-head"><div class="card-title">Modo</div></div>
      <div class="seg" data-modes>${MODES.map(([id, l]) => `<button data-mode="${id}" aria-pressed="false">${l}</button>`).join("")}</div>
      <p class="note" data-mode-help></p>
      <label class="check"><input type="checkbox" data-notes> Mostrar notificações no Mural</label>
    </article>
    <article class="card span-2">
      <div class="card-head"><div class="card-title">O que mostrar no modo Jogo</div></div>
      <div class="mural-opts" data-opts></div>
      <div data-fps-note></div>
    </article>
    <article class="card span-all">
      <div class="card-head"><div><div class="card-title">Capas dos jogos</div>
        <div class="card-sub">A arte que fica de fundo no modo Jogo. Vem da Steam, e você pode trocar.</div></div></div>
      <div class="toolbar"><input class="search" type="search" placeholder="Buscar jogo…" aria-label="Buscar jogo" data-q></div>
      <div class="cover-grid" data-games><p class="muted">Carregando…</p></div>
    </article>
  </div>`;

  el.addEventListener("click", onClick);
  el.addEventListener("change", onChange);
  el.addEventListener("input", onInput);
  el.addEventListener("error", e => {
    const img = e.target;
    if (img.tagName === "IMG" && img.dataset.letter) {
      const s = document.createElement("span");
      s.className = "cover-empty";
      s.textContent = img.dataset.letter;
      img.replaceWith(s);
    }
  }, true);
}

async function load() {
  try {
    [conf, games] = await Promise.all([app.api("/api/mural"), app.api("/api/mural/games").then(r => r.games)]);
    render();
  } catch (e) { toast(e.message, true); }
}

function render() {
  renderCity();
  $$("[data-mode]", root).forEach(b => b.setAttribute("aria-pressed", b.dataset.mode === conf.mode));
  $("[data-mode-help]", root).textContent = MODE_HELP[conf.mode];
  $("[data-notes]", root).checked = conf.notifications;
  $("[data-opts]", root).innerHTML = GAME_OPTS.map(([k, l]) =>
    `<label class="check"><input type="checkbox" data-opt="${k}" ${conf.game[k] ? "checked" : ""}> ${l}</label>`).join("");
  renderFps();
  renderGames();
}

function renderFps() {
  const g = app.snapshot?.data.game;
  $("[data-fps-note]", root).innerHTML = g && !g.enabled
    ? `<p class="note">FPS, 1% low e frametime dependem da leitura de FPS, que está desligada.</p>
       <button class="btn btn-sm" data-fps-on>Ligar a leitura de FPS</button>` : "";
}

function renderCity() {
  const c = $("[data-city]", root), loc = conf.location, w = conf.weather;
  c.innerHTML = `<div class="card-head"><div class="card-title">Cidade do clima</div></div>
    ${loc ? `<p class="mural-city-now"><b>${esc(loc.name)}</b> <span class="muted">${esc([loc.admin, loc.country].filter(Boolean).join(", "))}</span></p>
      ${w ? `<p class="note">Agora: ${Math.round(w.temp)}°, ${esc(w.label.toLowerCase())}.</p>` : ""}
      ${conf.weather_error ? `<div class="fan-alert warning">${esc(conf.weather_error)}</div>` : ""}`
      : `<p class="note">Escolha sua cidade para ver a temperatura no Mural.</p>`}
    <label class="stat-label" for="mural-city">${loc ? "Trocar de cidade" : "Buscar cidade"}</label>
    <input class="search mural-city-q" id="mural-city" type="search" placeholder="Nome da cidade" autocomplete="off" data-city-q>
    <ul class="city-list" data-cities></ul>
    <p class="note">Dados de clima: Open-Meteo. O PC envia só as coordenadas da cidade.</p>`;
}

async function searchCity(text) {
  const list = $("[data-cities]", root);
  if (text.trim().length < 2) { list.innerHTML = ""; return; }
  try {
    const { cities } = await app.api(`/api/mural/cities?q=${encodeURIComponent(text)}`);
    list.innerHTML = cities.map((c, i) => `<li><button class="btn btn-sm" data-pick="${i}">
      <b>${esc(c.name)}</b> <span class="muted">${esc([c.admin, c.country].filter(Boolean).join(", "))}</span></button></li>`).join("")
      || `<li class="muted">Nenhuma cidade encontrada. Confira o nome.</li>`;
    list._cities = cities;
  } catch (e) { list.innerHTML = `<li class="muted">${esc(e.message)}</li>`; }
}

async function save(body, done) {
  try {
    conf = await app.api("/api/mural", { method: "POST", body });
    if (done) toast(done);
  } catch (e) { toast(e.message, true); }
  render();
}

function onInput(e) {
  if (e.target.matches("[data-city-q]")) {
    clearTimeout(searchTimer);
    const v = e.target.value;
    searchTimer = setTimeout(() => searchCity(v), 350);
  } else if (e.target.matches("[data-q]")) {
    q = e.target.value.toLowerCase();
    renderGames();
  }
}

function onChange(e) {
  const t = e.target;
  if (t.dataset.opt) save({ game: { [t.dataset.opt]: t.checked } });
  else if (t.matches("[data-notes]")) save({ notifications: t.checked });
}

async function onClick(e) {
  if (e.target.closest("[data-open]")) { location.hash = "mural:abrir"; return openNow(true); }
  const mode = e.target.closest("[data-mode]");
  if (mode) return save({ mode: mode.dataset.mode });
  const pick = e.target.closest("[data-pick]");
  if (pick) {
    const c = $("[data-cities]", root)._cities[Number(pick.dataset.pick)];
    return save({ location: c }, `Clima de ${c.name} escolhido.`);
  }
  if (e.target.closest("[data-fps-on]")) {
    try { await app.api("/api/game", { method: "POST", body: { fps_enabled: true } }); toast("Leitura de FPS ligada."); $("[data-fps-note]", root).innerHTML = ""; }
    catch (err) { toast(err.message, true); }
    return;
  }
  const g = e.target.closest("[data-game]");
  if (g) openArt(games.find(x => x.key === g.dataset.game));
}

// ---------- capas ----------
function renderGames() {
  const box = $("[data-games]", root);
  const list = games.filter(g => !q || g.name.toLowerCase().includes(q));
  box.innerHTML = list.map(g => {
    const src = g.kind === "custom" ? artUrl(g) : g.steam ? artUrl(g, "header") : null;
    return `<button class="cover-tile" data-game="${esc(g.key)}">
      ${src ? `<img src="${src}" alt="" loading="lazy" data-letter="${esc(g.name[0] || "?")}">` : `<span class="cover-empty">${esc(g.name[0] || "?")}</span>`}
      <span class="cover-name">${esc(g.name)}</span>
      ${g.kind === "custom" ? `<span class="chip">Sua imagem</span>` : ""}</button>`;
  }).join("") || `<p class="muted">${q ? "Nenhum jogo encontrado." : "Nenhum jogo da Steam encontrado. Abra um jogo para ele aparecer aqui."}</p>`;
}

function openArt(g) {
  const sheet = $("#sheet");
  sheet.innerHTML = `<h2>${esc(g.name)}</h2>
    <p class="confirm-text">Escolha a arte de fundo no modo Jogo.</p>
    ${g.steam ? `<div class="art-choices">${ART_KINDS.map(([k, l]) => `<button class="art-choice" data-kind="${k}" aria-pressed="${g.kind === k}">
      <img src="${artUrl(g, k)}" alt="" onerror="this.closest('button').disabled=true"><span>${l}</span></button>`).join("")}</div>` : ""}
    ${g.kind === "custom" ? `<div class="art-choices"><div class="art-choice" aria-pressed="true"><img src="${artUrl(g)}" alt=""><span>Sua imagem</span></div></div>` : ""}
    <label class="btn art-upload">${ic("image")} Enviar uma imagem<input type="file" accept="image/*" data-upload hidden></label>
    <div class="sheet-actions">
      ${g.kind === "custom" ? `<button class="btn" data-art-reset>${g.steam ? "Voltar à arte da Steam" : "Remover imagem"}</button>` : ""}
      <button class="btn full" data-art-close>Fechar</button>
    </div>`;
  sheet.onclick = async e => {
    if (e.target.closest("[data-art-close]")) return sheet.close();
    const k = e.target.closest("[data-kind]");
    if (k) return setArt(g, { kind: k.dataset.kind });
    if (e.target.closest("[data-art-reset]")) {
      try { await app.api(`/api/mural/art/${encodeURIComponent(g.key)}/reset`, { method: "POST", body: {} }); toast("Arte restaurada."); }
      catch (err) { toast(err.message, true); }
      sheet.close();
      load();
    }
  };
  sheet.onchange = async e => {
    const f = e.target.files?.[0];
    if (!f) return;
    try { await setArt(g, { image: await shrink(f) }); }
    catch (err) { toast(err.message, true); }
  };
  sheet.addEventListener("close", () => { sheet.onclick = sheet.onchange = null; }, { once: true });
  sheet.showModal();
}

async function setArt(g, body) {
  try {
    await app.api(`/api/mural/art/${encodeURIComponent(g.key)}`, { method: "POST", body: { name: g.name, ...body } });
    toast("Arte salva.");
    $("#sheet").close();
    load();
  } catch (err) { toast(err.message, true); }
}

/** Reduz a imagem aqui no navegador: aceita qualquer formato que ele abra e cabe no limite do servidor. */
async function shrink(file) {
  let bmp;
  try { bmp = await createImageBitmap(file); }
  catch { throw new Error("Não foi possível abrir essa imagem. Tente um JPG ou PNG."); }
  const scale = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
  const c = document.createElement("canvas");
  c.width = Math.round(bmp.width * scale); c.height = Math.round(bmp.height * scale);
  c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
  for (const quality of [.85, .7, .55, .4]) {
    const url = c.toDataURL("image/jpeg", quality);
    if (url.length <= MAX_DATA) return url;
  }
  throw new Error("A imagem é grande demais, mesmo reduzida.");
}

// ---------- abrir ----------
function openNow(fullscreen) {
  return openStage(app, { fullscreen, exit: () => { if (location.hash === "#mural:abrir") location.hash = "mural"; } });
}

function route(sub) {
  if (sub === "abrir") openNow(false);
  else closeStage();
}

// hide: o botão Voltar ou outra aba também fecham o palco
export default { id: "mural", title: "Mural", icon: ic("frame-corners"), mount, route, show: load, hide: () => closeStage() };
