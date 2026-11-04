// O palco do Mural: a tela cheia em si (Início, Jogo, Música). A aba de configurações fica em modules/mural.js.
import { $, $$, bytes, duration, esc, pct } from "./ui.js";
import { ic, noteIcon } from "./icons.js";
import { artSrc, controlMedia, livePos, mediaButtons, mmss } from "./media.js";

const MODES = [["auto", "Automático"], ["home", "Início"], ["game", "Jogo"], ["music", "Música"]];
const IDLE = 4000;              // ms até esconder os controles
const CONF_EVERY = 5 * 60000;   // clima e preferências
const SHIFT_EVERY = 60000;      // desloca o conteúdo para não marcar telas OLED

let app, el, conf = null, scene = null, off = null, timers = [], idleTimer = null, noteTimer = null, lastSeq = null, wake = null;
let onExit = () => {};

const artUrl = (key, kind) => `/api/mural/art/${encodeURIComponent(key)}${kind ? `?kind=${kind}` : ""}`;
const deg = n => (n == null ? "—" : `${Math.round(n)}°`);

function clockTick() {
  const now = new Date();
  $$("[data-time]", el).forEach(n => { n.textContent = now.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }); });
  $$("[data-date]", el).forEach(n => { n.textContent = now.toLocaleDateString("pt-BR", { weekday: "long", day: "numeric", month: "long" }); });
}

function weatherMini() {
  const w = conf?.weather;
  return w ? `<span class="mural-mini-w">${ic(w.icon)} ${deg(w.temp)} <span>${esc(conf.location?.name || "")}</span></span>` : "";
}

function weatherBlock() {
  if (!conf.location) return `<button class="btn btn-primary" data-pick-city>Escolher cidade para ver o clima</button>`;
  const w = conf.weather;
  if (!w) return `<p class="mural-dim">${esc(conf.weather_error || "Buscando o clima…")}</p>`;
  const [today, ...rest] = w.days;
  return `<div class="mural-weather">
      <div class="mural-temp">${ic(w.icon)}<b>${deg(w.temp)}</b></div>
      <p class="mural-w-city">${esc(conf.location.name)}</p>
      <p class="mural-dim">${esc(w.label)}${today ? `. Máxima ${deg(today.max)}, mínima ${deg(today.min)}` : ""}</p>
      <p class="mural-dim">Sensação ${deg(w.feels)}, umidade ${w.humidity ?? "—"}%${today?.rain != null ? `, chuva ${today.rain}%` : ""}</p>
    </div>
    <ul class="mural-days">${rest.map(d => `<li>
      <span>${new Date(`${d.date}T12:00`).toLocaleDateString("pt-BR", { weekday: "short" }).replace(".", "")}</span>
      ${ic(d.icon)}<b>${deg(d.max)}</b><span class="mural-dim">${deg(d.min)}</span></li>`).join("")}</ul>`;
}

function setBackground(src) {
  const bg = $(".mural-bg", el);
  if (!src) { bg.hidden = true; bg.removeAttribute("src"); return; }
  if (bg.getAttribute("src") === src) return;
  bg.hidden = false;
  bg.onerror = () => { bg.hidden = true; };
  bg.src = src;
}

// ---------- escolha da cena ----------
function pick(d) {
  const game = d.game?.game, media = d.media?.current, mode = conf.mode;
  if (mode === "game") return game ? "game" : "home";
  if (mode === "music") return media ? "music" : "home";
  if (mode === "home") return "home";
  return game ? "game" : media?.status === "playing" ? "music" : "home";
}

function emptyNote(d) {
  if (conf.mode === "game" && !d.game?.game) return "Nenhum jogo aberto agora.";
  if (conf.mode === "music" && !d.media?.current) return "Nada tocando agora.";
  return "";
}

const SCENES = {
  home: () => `<div class="mural-home">
      <div class="mural-clock"><time data-time></time><p data-date></p><p class="mural-dim" data-empty></p></div>
      <div class="mural-side" data-weather></div>
    </div>`,
  game: () => `<div class="mural-game">
      <header class="mural-top"><span><time data-time></time></span><span data-mini></span></header>
      <div class="mural-title" data-title></div>
      <div class="mural-stats" data-stats></div>
      <div class="mural-player" data-player hidden></div>
    </div>`,
  music: () => `<div class="mural-music">
      <header class="mural-top"><span><time data-time></time> <span class="mural-dim" data-date></span></span><span data-mini></span></header>
      <div class="mural-track">
        <img class="mural-art" data-art alt="">
        <div class="mural-info">
          <h1 data-mtitle></h1><p class="mural-artist" data-artist></p><p class="mural-dim" data-album></p>
          <div class="mural-prog" data-prog><i></i></div><p class="mural-dim num" data-mtime></p>
          <div class="mural-btns" data-btns></div>
        </div>
      </div>
    </div>`,
};

function enter(name) {
  scene = name;
  el.dataset.scene = name;
  $(".mural-scene", el).innerHTML = SCENES[name]();
  clockTick();
  paintConf();
}

function paintConf() {
  const w = $("[data-weather]", el);
  if (w) w.innerHTML = weatherBlock();
  $$("[data-mini]", el).forEach(n => { n.innerHTML = weatherMini(); });
  $$("[data-mode]", el).forEach(b => b.setAttribute("aria-pressed", b.dataset.mode === conf.mode));
}

function cell(label, value, big = false) {
  return `<div class="mural-stat ${big ? "big" : ""}"><b>${value}</b><span>${label}</span></div>`;
}

function paintGame(d) {
  const g = d.game, game = g.game, o = conf.game;
  const t = $("[data-title]", el);
  if (t.dataset.key !== game.key) {
    t.dataset.key = game.key;
    setBackground(artUrl(game.key));
    const fallback = `<h1>${esc(game.name)}</h1>`;
    t.innerHTML = game.appid ? `<img class="mural-logo" src="${artUrl(game.key, "logo")}" alt="${esc(game.name)}">` : fallback;
    const logo = $("img", t);
    if (logo) logo.onerror = () => { t.innerHTML = fallback; };
    t.insertAdjacentHTML("beforeend", `<p class="mural-dim" data-since></p>`);
  }
  const since = $("[data-since]", t);
  if (since) since.textContent = o.session && game.since ? `Jogando há ${duration(Date.now() / 1000 - game.since)}` : "";

  const gpu = (d.gpu?.gpus || []).find(x => !x.integrated) || d.gpu?.gpus?.[0];
  const cpuT = d.cpu?.temp, s = [];
  if (o.fps && g.fps != null) s.push(cell("FPS", Math.round(g.fps), true));
  if (o.low1 && g.low1 != null) s.push(cell("1% low", Math.round(g.low1)));
  if (o.frametime && g.frametime != null) s.push(cell("Frametime", `${g.frametime.toFixed(1)} ms`));
  if (o.cpu && d.cpu) s.push(cell("CPU", pct(d.cpu.total)));
  if (o.gpu && gpu?.busy != null) s.push(cell("GPU", pct(gpu.busy)));
  if (o.temps && cpuT != null) s.push(cell("CPU", `${Math.round(cpuT)} °C`));
  if (o.temps && gpu?.temp != null) s.push(cell("GPU", `${Math.round(gpu.temp)} °C`));
  if (o.vram && gpu?.vram?.total) s.push(cell("VRAM", bytes(gpu.vram.used)));
  if (o.ram && d.memory) s.push(cell("RAM", pct(d.memory.percent)));
  $("[data-stats]", el).innerHTML = s.join("");

  const p = $("[data-player]", el), m = d.media?.current;
  const show = !!(o.music && m && m.status === "playing");
  p.hidden = !show;
  if (show) {
    const src = artSrc(m);
    p.innerHTML = `${src ? `<img src="${esc(src)}" alt="">` : `<span class="mural-noart">${ic("music-note")}</span>`}
      <div><b>${esc(m.title)}</b><span>${esc(m.artist || m.app)}</span></div>`;
  }
}

function paintMusic(d, ts) {
  const m = d.media.current;
  const src = artSrc(m);
  setBackground(src);
  const art = $("[data-art]", el);
  art.hidden = !src;
  if (src && art.getAttribute("src") !== src) { art.src = src; art.onerror = () => { art.hidden = true; }; }
  $("[data-mtitle]", el).textContent = m.title;
  $("[data-artist]", el).textContent = m.artist || m.app;
  $("[data-album]", el).textContent = m.album || "";
  const prog = $("[data-prog]", el), time = $("[data-mtime]", el);
  prog.hidden = time.hidden = !m.length;
  if (m.length) {
    const pos = livePos(m, ts);
    prog.firstElementChild.style.width = `${(pos / m.length) * 100}%`;
    time.textContent = `${mmss(pos)} / ${mmss(m.length)}`;
  }
  const btns = $("[data-btns]", el), key = `${m.id}|${m.status}|${JSON.stringify(m.can)}`;
  if (app.info.features.control && btns.dataset.key !== key) {
    btns.dataset.key = key;
    btns.innerHTML = mediaButtons(m);
  }
}

function paint(snap) {
  if (!conf) return;
  const d = snap.data, next = pick(d);
  if (next !== scene) enter(next);
  if (next === "game") paintGame(d);
  else if (next === "music") paintMusic(d, snap.ts);
  else {
    setBackground(null);
    const e = $("[data-empty]", el);
    if (e) e.textContent = emptyNote(d);
  }
  showNote(d.alerts?.latest);
}

// ---------- notificações ----------
function showNote(a) {
  const seq = a?.seq ?? 0;
  if (lastSeq === null) { lastSeq = seq; return; }
  if (seq <= lastSeq) return;
  lastSeq = seq;
  if (!conf.notifications || a.read) return;
  const n = $(".mural-note", el);
  n.className = `mural-note ${a.level}`;
  n.innerHTML = `<span class="mural-note-ico">${noteIcon(a.icon)}</span>
    <div><b>${esc(a.title)}</b>${a.text ? `<p>${esc(a.text)}</p>` : ""}${a.source ? `<small>${esc(a.source)}</small>` : ""}</div>`;
  n.hidden = false;
  clearTimeout(noteTimer);
  noteTimer = setTimeout(() => { n.hidden = true; }, (a.duration || 12) * 1000);
}

// ---------- controles ----------
function wakeUp() {
  el.classList.add("awake");
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => {
    if (!$(".mural-bar", el).contains(document.activeElement)) el.classList.remove("awake");
  }, IDLE);
}

async function setMode(mode) {
  conf = { ...conf, mode };
  paintConf();
  if (app.snapshot) paint(app.snapshot);
  try { conf = await app.api("/api/mural", { method: "POST", body: { mode } }); } catch { /* vale nesta tela até recarregar */ }
}

async function refreshConf() {
  try { conf = await app.api("/api/mural"); paintConf(); } catch { /* tenta no próximo ciclo */ }
}

function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen?.();
  else el.requestFullscreen?.().catch(() => {});
}

async function keepAwake() {
  // só em HTTPS ou localhost; pelo IP da rede o navegador não oferece a API
  try { wake = await navigator.wakeLock?.request("screen"); } catch { wake = null; }
}

function build() {
  el = document.createElement("div");
  el.className = "mural";
  el.setAttribute("role", "region");
  el.setAttribute("aria-label", "Mural");
  el.hidden = true;
  el.innerHTML = `<img class="mural-bg" alt="" hidden><div class="mural-scrim"></div>
    <div class="mural-scene"></div>
    <aside class="mural-note" role="status" aria-live="polite" hidden></aside>
    <nav class="mural-bar" aria-label="Controles do Mural">
      <div class="seg">${MODES.map(([id, label]) => `<button data-mode="${id}" aria-pressed="false">${label}</button>`).join("")}</div>
      <button class="btn" data-fs>${ic("frame-corners")} Tela cheia</button>
      <button class="btn" data-exit>${ic("x")} Sair</button>
    </nav>`;
  document.body.append(el);
  el.addEventListener("pointermove", wakeUp);
  el.addEventListener("pointerdown", wakeUp);
  el.addEventListener("focusin", wakeUp);
  el.addEventListener("click", async e => {
    const mode = e.target.closest("[data-mode]");
    if (mode) return setMode(mode.dataset.mode);
    if (e.target.closest("[data-fs]")) return toggleFullscreen();
    if (e.target.closest("[data-exit]")) return close();
    if (e.target.closest("[data-pick-city]")) return close();
    const b = e.target.closest("button[data-op]");
    if (b) {
      b.disabled = true;
      try { await controlMedia(app, b); } catch { /* o estado seguinte mostra o que valeu */ }
      b.disabled = false;
    }
  });
}

function onKey(e) {
  if (e.key !== "Escape") return wakeUp();
  if (document.fullscreenElement) document.exitFullscreen?.();     // alguns navegadores entregam o Esc à página
  else close();
}

export async function open(a, { fullscreen = false, exit = () => {} } = {}) {
  app = a; onExit = exit;
  if (!el) build();
  if (!el.hidden) return;
  el.hidden = false;
  document.body.classList.add("mural-open");
  if (fullscreen) el.requestFullscreen?.().catch(() => {});
  scene = null; lastSeq = null;
  await refreshConf();
  if (el.hidden) return;
  if (app.snapshot) paint(app.snapshot);
  off = app.onSnapshot(paint);
  timers = [setInterval(clockTick, 1000), setInterval(refreshConf, CONF_EVERY), setInterval(() => {
    const r = () => Math.round(Math.random() * 16 - 8);
    $(".mural-scene", el).style.translate = `${r()}px ${r()}px`;
  }, SHIFT_EVERY)];
  document.addEventListener("keydown", onKey);
  document.addEventListener("visibilitychange", keepAwake);
  keepAwake();
  wakeUp();
}

export function close() {
  if (!el || el.hidden) return;
  el.hidden = true;
  document.body.classList.remove("mural-open");
  off?.(); off = null;
  timers.forEach(clearInterval); timers = [];
  clearTimeout(idleTimer); clearTimeout(noteTimer);
  document.removeEventListener("keydown", onKey);
  document.removeEventListener("visibilitychange", keepAwake);
  if (document.fullscreenElement) document.exitFullscreen?.();
  wake?.release?.(); wake = null;
  onExit();
}
