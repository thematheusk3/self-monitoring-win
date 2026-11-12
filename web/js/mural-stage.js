// O palco do Mural: gerenciador de estado da tela cheia. Decide a cena (Início, Jogo, Música) a partir do que acontece no
// PC, aplica a paleta viva e entrega os eventos ao motor (mural-events.js). A aba de configurações fica em modules/mural.js.
import { $, $$, bytes, duration, esc } from "./ui.js";
import { ic } from "./icons.js";
import { artSrc, controlMedia, livePos, mediaButtons, mmss } from "./media.js";
import { Spark } from "./spark.js";
import { EventDeck, fromAlert } from "./mural-events.js";
import { paletteFrom, skyPalette } from "./mural-palette.js";
import { AURORA, BACKDROPS, Backdrop } from "./mural-backdrop.js";
import { currentPalette } from "./theme.js";

const MODES = [["auto", "Automático", "arrow-clockwise"], ["home", "Início", "house"], ["game", "Jogo", "game-controller"], ["music", "Música", "music-note"]];
// Aura: tela de bloqueio (relógio grande + uma cápsula de vidro) e player com a letra fluindo ao lado da capa.
// Duo: dois painéis sobre preto, como um relógio de cabeceira: a hora em numerais gigantes e um cartão que alterna.
// Cinema: a tela vira um quadro de filme, com faixas pretas em cima e embaixo; a letra da música entra como legenda.
// Painel: tudo de uma vez (relógio, clima, jogo, música e o PC), para um tablet ou monitor de canto.
// Clássico: o layout das primeiras versões.
export const LAYOUTS = [["aura", "Aura"], ["duo", "Duo"], ["cinema", "Cinema"], ["painel", "Painel"], ["classico", "Clássico"]];
const layout = () => (LAYOUTS.some(([id]) => id === conf?.layout) ? conf.layout : "aura");
const DUO_EVERY = 14000;        // ms de cada cartão no rodízio do Duo
const IDLE = 4000;              // ms até esconder os controles
const CONF_EVERY = 5 * 60000;   // clima e preferências
const SHIFT_EVERY = 60000;      // desloca o conteúdo para não marcar telas OLED
const SPLASH_SECONDS = 6;
const COVER_MINUTES = 2;        // tempo de cada capa no fundo "Capas dos jogos"

let app, el, deck, conf = null, scene = null, off = null, timers = [], idleTimer = null, lastSeq = null, wake = null;
let fpsSpark = null, lastGame = undefined, lastTrack = undefined, paletteKey = null, bgLayer = 0;
let onExit = () => {};
let backdrop = null, covers = null, coverAt = 0, minutes = 0;

const artUrl = (key, kind) => `/api/mural/art/${encodeURIComponent(key)}${kind ? `?kind=${kind}` : ""}`;
const deg = n => (n == null ? "—" : `${Math.round(n)}°`);

function clockTick() {
  const now = new Date();
  $$("[data-time]", el).forEach(n => { n.textContent = now.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }); });
  $$("[data-date]", el).forEach(n => { n.textContent = now.toLocaleDateString("pt-BR", { weekday: "long", day: "numeric", month: "long" }); });
  $$("[data-date-short]", el).forEach(n => { n.textContent = now.toLocaleDateString("pt-BR", { weekday: "short", day: "numeric", month: "short" }).replace(/\./g, ""); });
  $$("[data-hh]", el).forEach(n => { n.textContent = String(now.getHours()).padStart(2, "0"); });
  $$("[data-mm]", el).forEach(n => { n.textContent = String(now.getMinutes()).padStart(2, "0"); });
}

// ---------- paleta e fundo ----------
function applyPalette(p) {
  const root = document.documentElement.style;
  el.style.setProperty("--m-deep", p.deep);
  el.style.setProperty("--m-mid", p.mid);
  el.style.setProperty("--m-accent", p.accent);
  root.setProperty("--mural-accent", p.accent);      // o gráfico lê a cor do documento
}

async function paletteOf(src) {
  if (paletteKey === src) return;
  paletteKey = src;
  const p = await paletteFrom(src);
  if (p && paletteKey === src) applyPalette(p);
}

function homeView(kind) {
  if (kind === "aurora") return { palette: AURORA };
  if (kind === "board") return { palette: currentPalette() };
  const palette = skyPalette(conf.weather);
  return { palette, phase: palette.phase, weather: conf.weather };
}

async function loadCovers() {
  covers = [];                   // evita pedir duas vezes
  try {
    const { games } = await app.api("/api/mural/games");
    covers = games.filter(g => g.steam).map(g => g.key).sort(() => Math.random() - .5);
  } catch { /* fica com o céu */ }
  if (scene === "home") paintHome();
}

/** Fundo do Início: desenhado ou imagem. Sem capa ou sem imagem disponível, volta ao céu. */
function paintHome() {
  let kind = conf.background;
  if (kind === "games" && covers === null) loadCovers();
  if ((kind === "games" && !covers?.length) || (kind === "photo" && !conf.background_v)) kind = "sky";
  const src = kind === "games" ? artUrl(covers[coverAt % covers.length], "hero")
    : kind === "photo" ? `/api/mural/background?v=${conf.background_v}` : null;
  el.dataset.backdrop = kind;
  if (src) {
    backdrop.hide();
    setBackground(src);
    paletteOf(src);
    return;
  }
  setBackground(null);
  paletteKey = null;
  const view = homeView(kind);
  if (view.phase) el.dataset.sky = view.phase;
  applyPalette(view.palette);
  backdrop.show(kind, view);
}

function coverFailed() {
  if ((scene !== "home" && scene !== "board") || el.dataset.backdrop !== "games") return;
  covers.splice(coverAt % covers.length, 1);        // jogo sem arte de fundo: sai do rodízio
  paintHome();
}

function everyMinute() {
  if (scene !== "home" && scene !== "board") return;
  if (conf.background === "games" && covers?.length && ++minutes % COVER_MINUTES === 0) coverAt++;
  paintHome();                                       // o céu muda ao longo do dia
}

/** Troca de arte com fade: a imagem nova carrega na camada escondida e só então aparece. */
function setBackground(src) {
  const layers = $$(".mural-bg", el), cur = layers[bgLayer];
  if (!src) { layers.forEach(l => { l.classList.remove("on"); l.removeAttribute("src"); }); return; }
  if (cur.getAttribute("src") === src) return;
  const next = layers[1 - bgLayer];
  next.onload = () => { next.classList.add("on"); cur.classList.remove("on"); bgLayer = 1 - bgLayer; };
  next.onerror = () => { cur.classList.remove("on"); coverFailed(); };
  next.src = src;
}

// ---------- blocos ----------
function weatherMini() {
  const w = conf?.weather;
  return w ? `<span class="mural-chip">${ic(w.icon)} ${deg(w.temp)} <span>${esc(conf.location?.name || "")}</span></span>` : "";
}

function weatherBlock() {
  if (!conf.location) return `<button class="btn btn-primary" data-pick-city>Escolher cidade para ver o clima</button>`;
  const w = conf.weather;
  if (!w) return `<p class="mural-dim">${esc(conf.weather_error || "Buscando o clima…")}</p>`;
  const [today, ...rest] = w.days;
  return `<section class="mural-glass mural-weather">
      <div class="mural-w-now">
        <div class="mural-temp">${ic(w.icon)}<b>${deg(w.temp)}</b></div>
        <div><p class="mural-w-city">${esc(conf.location.name)}</p><p class="mural-dim">${esc(w.label)}</p></div>
      </div>
      <dl class="mural-w-facts">
        ${today ? `<div><dt>Máx.</dt><dd>${deg(today.max)}</dd></div><div><dt>Mín.</dt><dd>${deg(today.min)}</dd></div>` : ""}
        <div><dt>Sensação</dt><dd>${deg(w.feels)}</dd></div><div><dt>Umidade</dt><dd>${w.humidity ?? "—"}%</dd></div>
        ${today?.rain != null ? `<div><dt>Chuva</dt><dd>${today.rain}%</dd></div>` : ""}
      </dl>
      <ul class="mural-days">${rest.map(d => `<li>
        <span>${new Date(`${d.date}T12:00`).toLocaleDateString("pt-BR", { weekday: "short" }).replace(".", "")}</span>
        ${ic(d.icon)}<b>${deg(d.max)}</b><span class="mural-dim">${deg(d.min)}</span></li>`).join("")}</ul>
    </section>`;
}

/** Clima na faixa de baixo do Cinema: agora + próximos dias, em linha. */
function weatherStrip() {
  if (!conf.location) return `<button class="btn btn-primary" data-pick-city>Escolher cidade para ver o clima</button>`;
  const w = conf.weather;
  if (!w) return `<p class="mural-dim">${esc(conf.weather_error || "Buscando o clima…")}</p>`;
  const [today, ...rest] = w.days;
  return `<div class="cine-w-now">${ic(w.icon)}<b>${deg(w.temp)}</b>
      <span><em>${esc(conf.location.name)}</em>${esc(w.label)}${today ? ` · ${deg(today.max)} / ${deg(today.min)}` : ""}</span></div>
    <ul class="cine-days">${rest.map(d => `<li><span>${new Date(`${d.date}T12:00`).toLocaleDateString("pt-BR", { weekday: "short" }).replace(".", "")}</span>
      ${ic(d.icon)}<b>${deg(d.max)}</b></li>`).join("")}</ul>`;
}

const weekday = d => new Date(`${d.date}T12:00`).toLocaleDateString("pt-BR", { weekday: "short" }).replace(".", "");

/** Clima da Aura e do Duo: agora (ícone, temperatura, cidade) e os próximos três dias. */
function weatherAura() {
  if (!conf.location) return `<button class="aura-link" data-pick-city>Escolher cidade para ver o clima</button>`;
  const w = conf.weather;
  if (!w) return `<p class="aura-wait">${esc(conf.weather_error || "Buscando o clima…")}</p>`;
  const [today, ...rest] = w.days;
  return `<div class="aura-wnow">${ic(w.icon)}<b>${deg(w.temp)}</b>
      <p><span>${esc(conf.location.name)}</span><span>${esc(w.label)}</span>${today ? `<span>${deg(today.max)} / ${deg(today.min)}</span>` : ""}</p></div>
    <ul class="aura-days">${rest.slice(0, 3).map(d => `<li><span>${weekday(d)}</span>${ic(d.icon)}<b>${deg(d.max)}</b><i>${deg(d.min)}</i></li>`).join("")}</ul>`;
}

// Anéis concêntricos (de fora para dentro: CPU, GPU, RAM). Criados uma vez e atualizados no lugar, para o arco deslizar.
const RINGS = [["cpu", "CPU"], ["gpu", "GPU"], ["ram", "RAM"]];

function pcValues(d, o = { cpu: true, gpu: true, ram: true }) {
  const gpu = (d.gpu?.gpus || []).find(x => !x.integrated) || d.gpu?.gpus?.[0];
  return { cpu: o.cpu && d.cpu ? d.cpu.total : null, gpu: o.gpu && gpu?.busy != null ? gpu.busy : null,
    ram: o.ram && d.memory ? d.memory.percent : null };
}

function paintRings(box, vals) {
  if (!box) return;
  const keys = RINGS.filter(([k]) => vals[k] != null);
  const sig = keys.map(([k]) => k).join();
  if (box.dataset.sig !== sig) {
    box.dataset.sig = sig;
    box.hidden = !keys.length;
    box.innerHTML = keys.length ? `<div class="aura-rings"><svg viewBox="0 0 100 100" aria-hidden="true">${keys.map(([k], i) => {
      const r = 43.5 - i * 12.5;
      return `<circle class="t ${k}" cx="50" cy="50" r="${r}"/><circle class="v ${k}" data-ring="${k}" cx="50" cy="50" r="${r}" pathLength="100" stroke-dasharray="0 100"/>`;
    }).join("")}</svg>
      <dl>${keys.map(([k, l]) => `<div class="${k}"><dt>${l}</dt><dd data-ringv="${k}"></dd></div>`).join("")}</dl></div>` : "";
  }
  for (const [k] of keys) {
    const v = Math.max(0, Math.min(100, vals[k]));
    $(`[data-ring="${k}"]`, box).setAttribute("stroke-dasharray", `${v} 100`);
    $(`[data-ringv="${k}"]`, box).textContent = `${Math.round(v)}%`;
  }
}

/** Anel de 0 a 100: mostra o nível de relance, sem precisar ler o número. */
function ring(value, label, suffix = "%") {
  const v = Math.max(0, Math.min(100, value ?? 0));
  return `<div class="mural-ring"><svg viewBox="0 0 100 100" aria-hidden="true">
      <circle cx="50" cy="50" r="42" pathLength="100"/><circle class="on" cx="50" cy="50" r="42" pathLength="100" stroke-dasharray="${v} 100"/></svg>
    <b>${Math.round(value ?? 0)}${suffix}</b><span>${label}</span></div>`;
}

const cell = (label, value) => `<div class="mural-stat"><b>${value}</b><span>${label}</span></div>`;

// ---------- escolha da cena ----------
function pick(d) {
  if (layout() === "painel") return "board";
  if (layout() === "duo") return "duo";
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
      <header class="mural-top"><span class="mural-chip"><time data-time></time></span><span data-mini></span></header>
      <div class="mural-title" data-title></div>
      <section class="mural-glass mural-hud">
        <div class="mural-fps" data-fps-box hidden><div class="mural-fps-num"><b data-fps></b><span>FPS</span></div><div class="spark" data-spark></div></div>
        <div class="mural-stats" data-stats></div>
      </section>
      <div class="mural-glass mural-player" data-player hidden></div>
    </div>`,
  music: () => `<div class="mural-music">
      <header class="mural-top"><span class="mural-chip"><time data-time></time> <span data-date></span></span><span data-mini></span></header>
      <div class="mural-track">
        <img class="mural-art" data-art alt="">
        <div class="mural-info">
          <h1 data-mtitle></h1><p class="mural-artist" data-artist></p><p class="mural-dim" data-album></p>
          <div class="mural-lyrics" data-lyrics hidden aria-live="off"><p data-l="0"></p><p data-l="1"></p><p data-l="2"></p></div>
          <div class="mural-prog" data-prog><i></i></div><p class="mural-dim num" data-mtime></p>
          <div class="mural-btns" data-btns></div>
          <div class="mural-vol" data-mvol hidden></div>
        </div>
      </div>
    </div>`,
};

// Mesmos ganchos (data-*) dos modelos clássicos: paintGame/paintMusic servem aos três layouts.
const CINEMA = {
  home: () => `<div class="cine cine-home">
      <header class="cine-bar cine-top"><span class="cine-date" data-date></span><span class="cine-empty" data-empty></span></header>
      <div class="cine-frame"><time class="cine-clock" data-time></time></div>
      <footer class="cine-bar cine-bottom cine-weather" data-weather></footer>
    </div>`,
  game: () => `<div class="cine cine-game">
      <header class="cine-bar cine-top">
        <div class="cine-title mural-title" data-title></div>
        <div class="cine-right"><div class="cine-player mural-player" data-player hidden></div><time data-time></time><span data-mini></span></div>
      </header>
      <div class="cine-frame"></div>
      <footer class="cine-bar cine-bottom">
        <div class="mural-fps" data-fps-box hidden><div class="mural-fps-num"><b data-fps></b><span>FPS</span></div><div class="spark" data-spark></div></div>
        <div class="mural-stats" data-stats></div>
      </footer>
    </div>`,
  music: () => `<div class="cine cine-music">
      <header class="cine-bar cine-top">
        <div class="cine-card"><h1 data-mtitle></h1><p><span data-artist></span></p></div>
        <div class="cine-right"><time data-time></time><span data-mini></span></div>
      </header>
      <div class="cine-frame">
        <img class="cine-art" data-art alt="">
        <div class="cine-ctl"><div class="mural-btns" data-btns></div><div class="mural-vol" data-mvol hidden></div></div>
        <div class="cine-prog mural-prog" data-prog><i></i></div>
      </div>
      <footer class="cine-bar cine-bottom">
        <div class="cine-subs mural-lyrics" data-lyrics hidden aria-live="off"><p data-l="0"></p><p data-l="1"></p><p data-l="2"></p></div>
        <p class="cine-meta"><span data-album></span><span class="num" data-mtime></span></p>
      </footer>
    </div>`,
};
const BOARD = () => `<div class="board">
    <section class="board-clock"><time data-time></time><p data-date></p></section>
    <section class="board-weather" data-weather></section>
    <section class="board-tile board-game" data-board-game hidden>
      <img class="board-game-art" data-game-art alt="">
      <div class="board-game-in">
        <div class="mural-title" data-title></div>
        <div class="mural-fps" data-fps-box hidden><div class="mural-fps-num"><b data-fps></b><span>FPS</span></div><div class="spark" data-spark></div></div>
        <div class="mural-stats" data-stats></div>
      </div>
    </section>
    <section class="board-tile board-music" data-board-music hidden>
      <img class="board-art" data-art alt="">
      <div class="board-music-in">
        <h2 data-mtitle></h2><p class="board-artist"><span data-artist></span></p><p class="mural-dim" data-album></p>
        <div class="board-lyric mural-lyrics" data-lyrics hidden aria-live="off"><p data-l="0"></p><p data-l="1"></p><p data-l="2"></p></div>
        <div class="mural-prog" data-prog><i></i></div><p class="mural-dim num" data-mtime></p>
        <div class="mural-btns" data-btns></div><div class="mural-vol" data-mvol hidden></div>
      </div>
    </section>
    <section class="board-tile board-pc" data-board-pc></section>
  </div>`;

// Aura: Início e Jogo têm a estrutura da tela de bloqueio (hora em cima, uma cápsula de vidro embaixo);
// Música é o player, com a letra inteira fluindo (data-lyrics-flow) em vez de três versos fixos.
const AURA = {
  home: () => `<div class="aura aura-home">
      <header class="aura-lock"><p class="aura-date" data-date></p><time class="aura-clock" data-time></time>
        <p class="aura-note" data-empty></p></header>
      <footer class="aura-dock">
        <div class="aura-pill" data-player hidden></div>
        <section class="aura-glass aura-capsule"><div class="aura-seg aura-weather" data-weather></div><div class="aura-seg" data-pc></div></section>
      </footer>
    </div>`,
  game: () => `<div class="aura aura-game">
      <header class="aura-status"><time data-time></time><span data-date-short></span>
        <div class="aura-pill" data-player hidden></div></header>
      <div class="aura-title mural-title" data-title></div>
      <footer class="aura-dock"><section class="aura-glass aura-capsule">
        <div class="aura-seg aura-fps" data-fps-box hidden><p><b data-fps></b><span>FPS</span></p><div class="spark" data-spark></div></div>
        <div class="aura-seg" data-pc></div>
        <div class="aura-seg aura-cells" data-stats></div>
      </section></footer>
    </div>`,
  music: () => `<div class="aura aura-music" data-aura-music>
      <div class="aura-player">
        <div class="aura-artbox" data-artbox><img class="aura-art" data-art alt=""></div>
        <div class="aura-meta">
          <h1 data-mtitle></h1><p class="aura-artist" data-artist></p>
          <div class="mural-prog aura-prog" data-prog><i></i></div>
          <p class="aura-times"><span data-mpos></span><span data-mrem></span></p>
          <div class="mural-btns aura-btns" data-btns></div>
          <div class="mural-vol aura-vol" data-mvol hidden></div>
        </div>
      </div>
      <div class="aura-lyrics" data-lyrics data-lyrics-flow hidden aria-live="off"><div class="aura-lyrics-track"></div></div>
      <time class="aura-corner" data-time></time>
    </div>`,
};
// Duo: o relógio à esquerda e, à direita, um cartão por vez (data-duo-stack); os cartões ficam em DUO_CARDS.
const DUO = () => `<div class="duo">
    <section class="duo-pane duo-clock"><p class="duo-date" data-date-short></p>
      <div class="duo-digits"><b data-hh></b><b data-mm></b></div></section>
    <section class="duo-pane duo-stack" data-duo-stack></section>
    <div class="duo-dots" data-duo-dots></div>
  </div>`;
const TEMPLATES = { aura: AURA, cinema: CINEMA, classico: SCENES };

function enter(name) {
  scene = name;
  el.dataset.scene = name;
  el.dataset.layout = layout();
  fpsSpark = null;
  duo.cur = null;
  if (name !== "home" && name !== "board") { backdrop.hide(); delete el.dataset.backdrop; }
  if (name === "duo") setBackground(null);                 // fundo preto: sem arte nem céu atrás dos painéis
  $("[data-bg]", el).hidden = name !== "home" && name !== "board";
  $(".mural-scene", el).innerHTML = name === "board" ? BOARD() : name === "duo" ? DUO() : (TEMPLATES[layout()] || SCENES)[name]();
  clockTick();
  paintConf();
}

function paintConf() {
  const w = $("[data-weather]", el), lay = layout();
  if (w) w.innerHTML = lay === "aura" || lay === "duo" ? weatherAura() : lay === "cinema" && scene === "home" ? weatherStrip() : weatherBlock();
  $$("[data-mini]", el).forEach(n => { n.innerHTML = weatherMini(); });
  $$("[data-mode]", el).forEach(b => b.setAttribute("aria-pressed", b.dataset.mode === conf.mode));
  movePill();
  if (scene === "home" || scene === "board") paintHome();
}

/** O destaque desliza até o modo escolhido: mostra de onde veio e para onde foi. */
function movePill() {
  const b = $("[data-mode][aria-pressed='true']", el), pill = $(".mural-dock-pill", el);
  if (!b || !pill) return;
  pill.style.width = `${b.offsetWidth}px`;
  pill.style.translate = `${b.offsetLeft}px 0`;
}

function paintGame(d) {
  const g = d.game, game = g.game, o = conf.game;
  const t = $("[data-title]", el);
  if (t.dataset.key !== game.key) {
    t.dataset.key = game.key;
    const src = artUrl(game.key);
    const tileArt = $("[data-game-art]", el);
    if (tileArt) tileArt.src = src;
    else { setBackground(src); paletteOf(src); }
    const fallback = `<h1>${esc(game.name)}</h1>`;
    t.innerHTML = game.appid ? `<img class="mural-logo" src="${artUrl(game.key, "logo")}" alt="${esc(game.name)}">` : fallback;
    const logo = $("img", t);
    if (logo) logo.onerror = () => { logo.replaceWith(Object.assign(document.createElement("h1"), { textContent: game.name })); };
    t.insertAdjacentHTML("beforeend", `<p class="mural-dim" data-since></p>`);
  }
  const since = $("[data-since]", t);
  if (since) since.textContent = o.session && game.since ? `Jogando há ${duration(Date.now() / 1000 - game.since)}` : "";

  const box = $("[data-fps-box]", el), hasFps = o.fps && g.fps != null;
  box.hidden = !hasFps;
  if (hasFps) {
    $("[data-fps]", el).textContent = Math.round(g.fps);
    fpsSpark ||= new Spark($("[data-spark]", el), { series: [{ key: "game.fps", color: ["aura", "duo"].includes(layout()) ? "--mural-ink" : "--mural-accent" }], min: 0, format: v => `${Math.round(v)} FPS` });
    fpsSpark.setData(app.history);
  }

  const gpu = (d.gpu?.gpus || []).find(x => !x.integrated) || d.gpu?.gpus?.[0];
  const cpuT = d.cpu?.temp, s = [];
  const rings = $("[data-pc]", el);          // Aura e Duo: uso em anéis concêntricos, fora da lista de números
  if (rings) paintRings(rings, pcValues(d, o));
  else {
    if (o.cpu && d.cpu) s.push(ring(d.cpu.total, "CPU"));
    if (o.gpu && gpu?.busy != null) s.push(ring(gpu.busy, "GPU"));
    if (o.ram && d.memory) s.push(ring(d.memory.percent, "RAM"));
  }
  if (o.low1 && g.low1 != null) s.push(cell("1% low", Math.round(g.low1)));
  if (o.frametime && g.frametime != null) s.push(cell("Frametime", `${g.frametime.toFixed(1)} ms`));
  if (o.temps && cpuT != null) s.push(cell("CPU", `${Math.round(cpuT)} °C`));
  if (o.temps && gpu?.temp != null) s.push(cell("GPU", `${Math.round(gpu.temp)} °C`));
  if (o.vram && gpu?.vram?.total) s.push(cell("VRAM", bytes(gpu.vram.used)));
  const stats = $("[data-stats]", el);
  stats.innerHTML = s.join("");
  stats.hidden = !s.length;
  paintMini(d, o.music);
}

/** Música tocando, em miniatura (na cena do jogo e no Início da Aura). */
function paintMini(d, wanted = true) {
  const p = $("div[data-player]", el), m = d.media?.current;
  if (!p) return;
  const show = !!(wanted && m && m.status === "playing");
  p.hidden = !show;
  const key = show ? `${m.id}|${m.title}|${m.artist}` : "";
  if (p.dataset.key === key) return;
  p.dataset.key = key;
  if (show) {
    const src = artSrc(m);
    p.innerHTML = `${src ? `<img src="${esc(src)}" alt="">` : `<span class="mural-noart">${ic("music-note")}</span>`}
      <div><b>${esc(m.title)}</b><span>${esc(m.artist || m.app)}</span></div>`;
  }
}

function paintMusic(d, ts) {
  const m = d.media.current;
  const src = artSrc(m);
  if (scene !== "board" && scene !== "duo") setBackground(src);
  if (src && scene !== "board") paletteOf(src);
  const art = $("[data-art]", el);
  art.hidden = !src;
  if (src && art.getAttribute("src") !== src) { art.src = src; art.onerror = () => { art.hidden = true; }; }
  $("[data-artbox]", el)?.classList.toggle("paused", m.status !== "playing");      // a capa encolhe quando pausa
  $("[data-mtitle]", el).textContent = m.title;
  $("[data-artist]", el).textContent = m.artist || m.app;
  const album = $("[data-album]", el), time = $("[data-mtime]", el), prog = $("[data-prog]", el);
  if (album) album.textContent = m.album || "";
  prog.hidden = !m.length;
  if (time) time.hidden = !m.length;
  const pos = m.length ? livePos(m, ts) : 0;
  if (m.length) {
    prog.firstElementChild.style.width = `${(pos / m.length) * 100}%`;
    if (time) time.textContent = `${mmss(pos)} / ${mmss(m.length)}`;
  }
  const mpos = $("[data-mpos]", el);
  if (mpos) {                                    // Aura: tempo decorrido à esquerda e o que falta à direita
    mpos.parentElement.hidden = !m.length;
    mpos.textContent = mmss(pos);
    $("[data-mrem]", el).textContent = `−${mmss(Math.max(0, m.length - pos))}`;
  }
  const btns = $("[data-btns]", el), key = `${m.id}|${m.status}|${m.shuffle}|${m.repeat}|${JSON.stringify(m.can)}`;
  if (app.info.features.control && btns.dataset.key !== key) {
    btns.dataset.key = key;
    btns.innerHTML = mediaButtons(m, { seek: true, extras: true });
  }
  if (app.info.features.control) appVolume(m.id);
  lyricsFor(m, ts);
}

// ---------- letra sincronizada (LRCLIB, pelo backend) ----------
let lyr = { key: null, data: null, m: null, ts: 0, recv: 0, timer: null, line: -2 };

async function lyricsFor(m, ts) {
  lyr.m = m; lyr.ts = ts; lyr.recv = performance.now();
  const key = `${m.id}|${m.title}|${m.artist}`;
  if (!lyr.timer) lyr.timer = setInterval(lyricsTick, 250);
  if (key === lyr.key) {          // a cena pode ter sido refeita (saiu e voltou para Música)
    if (lyr.data && $("[data-lyrics]", el)?.hidden) { lyr.line = -2; lyricsTick(); }
    return;
  }
  lyr.key = key; lyr.data = null; lyr.line = -2;
  paintLyrics();
  if (conf.lyrics === false || !m.length) return;
  try {
    const r = await app.api(`/api/media/lyrics?player=${encodeURIComponent(m.id)}`);
    if (lyr.key === key && r.synced?.length) { lyr.data = r.synced; lyricsTick(); }
  } catch { /* sem letra */ }
}

function lyricsTick() {
  if (conf?.lyrics === false && lyr.key) { lyr.key = null; lyr.data = null; paintLyrics(); return; }
  if (!["music", "board", "duo"].includes(scene) || !lyr.data || !lyr.m) return;
  const pos = livePos(lyr.m, lyr.ts + (performance.now() - lyr.recv) / 1000) + 0.15;   // verso um tiquinho antes
  let i = -1;
  for (let k = 0; k < lyr.data.length && lyr.data[k][0] <= pos; k++) i = k;
  const box = $("[data-lyrics-flow]", el);
  if (i !== lyr.line || (box && box.clientHeight !== lyr.h)) { lyr.line = i; paintLyrics(); }
}

const DOTS = `<span class="aura-dots" aria-hidden="true"><i></i><i></i><i></i></span>`;

/** Aura: a letra inteira numa coluna que desliza; o verso atual fica nítido e os outros apagam e desfocam com a distância. */
function paintFlow(box) {
  const on = !!lyr.data, track = box.firstElementChild;
  box.hidden = !on;
  $("[data-aura-music]", el)?.classList.toggle("has-lyrics", on);
  if (!on) { box.dataset.key = ""; track.innerHTML = ""; return; }
  if (box.dataset.key !== lyr.key) {             // letra nova: uma linha por verso (a primeira é a introdução)
    box.dataset.key = lyr.key;
    track.innerHTML = `<p data-i="-1">${DOTS}</p>` + lyr.data.map(([, text], i) => `<p data-i="${i}">${text ? esc(text) : DOTS}</p>`).join("");
  }
  const lines = track.children, cur = lines[lyr.line + 1];
  for (let j = 0; j < lines.length; j++) {
    const d = j - (lyr.line + 1), a = Math.abs(d), n = lines[j];
    n.classList.toggle("on", d === 0);
    n.classList.toggle("far", a > 7);
    n.style.setProperty("--a", Math.min(a, 6));
    n.style.setProperty("--d", Math.max(-2, Math.min(d, 8)));
  }
  lyr.h = box.clientHeight;
  if (cur) track.style.setProperty("--y", `${Math.round(lyr.h * .36 - cur.offsetTop - cur.offsetHeight / 2)}px`);
}

function paintLyrics() {
  const box = $("[data-lyrics]", el);
  if (!box) return;
  if (box.hasAttribute("data-lyrics-flow")) return paintFlow(box);
  const on = !!lyr.data;
  box.hidden = !on;
  $(".mural-track", el)?.classList.toggle("has-lyrics", on);
  if (!on) return;
  const v = j => (j < 0 ? "" : j >= lyr.data.length ? "" : lyr.data[j][1] || "♪");
  const i = lyr.line;
  const ps = box.children;
  ps[0].textContent = v(i - 1);
  ps[1].textContent = i < 0 ? (lyr.data[0]?.[0] > 3 ? "♪" : "") : v(i);
  ps[2].textContent = v(i + 1);
  box.classList.remove("step"); void box.offsetWidth; box.classList.add("step");
}

// ---------- volume do app que está tocando (o mesmo do Mixer de volume; não mexe no volume geral) ----------
let vol = { player: null, at: 0, data: null, busy: false, dragging: false, timer: null };

async function appVolume(player, force = false) {
  const now = Date.now();
  if (vol.busy || (!force && player === vol.player && now - vol.at < 5000)) return;
  if (player !== vol.player) vol.data = null;
  vol.player = player; vol.at = now; vol.busy = true;
  try { vol.data = (await app.api(`/api/media/audio?player=${encodeURIComponent(player)}`)).audio; } catch { /* mantém o anterior */ }
  vol.busy = false;
  paintVolume();
}

function paintVolume() {
  const box = $("[data-mvol]", el);
  if (!box) return;
  const a = vol.data;
  box.hidden = !a;
  if (!a || vol.dragging) return;
  if (box.dataset.index !== String(a.index)) {
    box.dataset.index = a.index;
    box.innerHTML = `<button class="btn btn-sm mute" data-mvol-mute></button>
      <input type="range" min="0" max="100" step="1" data-mvol-range aria-label="Volume do ${esc(a.app)}"
        title="Volume do ${esc(a.app)} (só este app; o volume geral do Windows não muda)">
      <output data-mvol-out></output>`;
  }
  const muteBtn = $("[data-mvol-mute]", box);
  muteBtn.innerHTML = ic(a.mute ? "speaker-slash" : "speaker-high");
  muteBtn.classList.toggle("on", a.mute);
  muteBtn.setAttribute("aria-pressed", a.mute);
  muteBtn.title = muteBtn.ariaLabel = a.mute ? "Ativar o som do app" : "Mudo só neste app";
  const range = $("[data-mvol-range]", box);
  range.value = a.volume;
  range.style.setProperty("--v", `${a.volume}%`);        // a Aura pinta a parte preenchida da barra
  $("[data-mvol-out]", box).textContent = `${a.volume}%`;
}

function setVolume(v) {
  if (!vol.data) return;
  vol.data.volume = v;
  $("[data-mvol-range]", el).style.setProperty("--v", `${v}%`);
  $("[data-mvol-out]", el).textContent = `${v}%`;
  clearTimeout(vol.timer);
  vol.timer = setTimeout(async () => {
    try { await app.api("/api/audio", { method: "POST", body: { op: "volume", kind: "app", index: vol.data.index, volume: v } }); }
    catch { appVolume(vol.player, true); }
  }, 120);
}

async function toggleAppMute() {
  if (!vol.data) return;
  const mute = !vol.data.mute;
  try {
    await app.api("/api/audio", { method: "POST", body: { op: "mute", kind: "app", index: vol.data.index, mute } });
    vol.data.mute = mute;
    paintVolume();
  } catch { appVolume(vol.player, true); }
}

/** Painel: tudo de uma vez. Jogo e música só aparecem quando existem; o PC está sempre lá. */
function paintBoard(d, ts) {
  const board = $(".board", el), game = d.game?.game, m = d.media?.current;
  const g = $("[data-board-game]", el), mu = $("[data-board-music]", el);
  g.hidden = !game;
  mu.hidden = !(m && m.title);
  board.dataset.has = `${game ? "g" : ""}${m && m.title ? "m" : ""}` || "none";
  if (game) paintGame(d);
  else { const t = $("[data-title]", el); if (t) t.dataset.key = ""; }
  if (m && m.title) paintMusic(d, ts);
  const gpu = (d.gpu?.gpus || []).find(x => !x.integrated) || d.gpu?.gpus?.[0];
  const pc = [];
  if (d.cpu) pc.push(ring(d.cpu.total, "CPU"));
  if (gpu?.busy != null) pc.push(ring(gpu.busy, "GPU"));
  if (d.memory) pc.push(ring(d.memory.percent, "RAM"));
  const temps = [d.cpu?.temp != null && cell("CPU", `${Math.round(d.cpu.temp)} °C`), gpu?.temp != null && cell("GPU", `${Math.round(gpu.temp)} °C`),
    gpu?.vram?.total && cell("VRAM", bytes(gpu.vram.used))].filter(Boolean);
  $("[data-board-pc]", el).innerHTML = `<div class="board-rings">${pc.join("")}</div><div class="mural-stats">${temps.join("")}</div>`;
}

// ---------- Duo: um cartão por vez ao lado do relógio ----------
let duo = { cur: null, dots: "" };
const DUO_CARDS = {
  game: () => `<article class="duo-card duo-game"><img class="duo-bgart" data-game-art alt="">
      <div class="duo-in"><div class="duo-title mural-title" data-title></div>
        <div class="duo-fps" data-fps-box hidden><p><b data-fps></b><span>FPS</span></p><div class="spark" data-spark></div></div>
        <div class="duo-row"><div data-pc></div><div class="duo-cells" data-stats></div></div></div></article>`,
  music: () => `<article class="duo-card duo-music"><img class="duo-bgart" data-art-bg alt="">
      <div class="duo-in"><div class="duo-head"><div class="aura-artbox" data-artbox><img class="duo-art" data-art alt=""></div>
          <div class="duo-meta"><h2 data-mtitle></h2><p data-artist></p></div></div>
        <div class="duo-lyric mural-lyrics" data-lyrics hidden aria-live="off"><p data-l="0"></p><p data-l="1"></p><p data-l="2"></p></div>
        <div class="duo-foot"><div class="mural-prog aura-prog" data-prog><i></i></div>
          <div class="mural-btns aura-btns" data-btns></div></div></div></article>`,
  weather: () => `<article class="duo-card duo-weather"><div class="duo-in aura-weather" data-weather></div></article>`,
  pc: () => `<article class="duo-card duo-pc"><div class="duo-in"><div data-pc></div><div class="duo-cells" data-stats-pc></div></div></article>`,
};

/** Cartões que cabem agora. O modo fixa um (Jogo, Música) ou restringe o rodízio (Início = clima e PC). */
function duoList(d) {
  const m = d.media?.current;
  const has = { game: !!d.game?.game, music: !!(m && m.title), weather: !!conf.weather, pc: true };
  const all = ["game", "music", "weather", "pc"].filter(k => has[k]);
  if (conf.mode === "game" && has.game) return ["game"];
  if (conf.mode === "music" && has.music) return ["music"];
  if (conf.mode === "home") return all.filter(k => k === "weather" || k === "pc");
  return all;
}

function paintDuo(d, ts) {
  const list = duoList(d), id = list[Math.floor(Date.now() / DUO_EVERY) % list.length];
  const stack = $("[data-duo-stack]", el);
  if (id !== duo.cur) {
    const old = $(".duo-card:not(.out)", stack);
    if (old) {              // o cartão que sai perde os ganchos: os pintores só enxergam o novo
      old.querySelectorAll("*").forEach(n => [...n.attributes].forEach(a => {
        if (a.name.startsWith("data-") && a.name !== "data-l") n.removeAttribute(a.name);
      }));
      old.classList.add("out");
      setTimeout(() => old.remove(), 900);
    }
    stack.insertAdjacentHTML("beforeend", DUO_CARDS[id]());
    duo.cur = id;
    fpsSpark = null;
    if (id === "weather") paintConf();
  }
  const dots = list.length > 1 ? list.map(k => `<i class="${k === id ? "on" : ""}"></i>`).join("") : "";
  if (dots !== duo.dots) { duo.dots = dots; $("[data-duo-dots]", el).innerHTML = dots; }

  if (id === "game") {
    paintGame(d);
    paletteOf(artUrl(d.game.game.key));
  } else if (id === "music") {
    paintMusic(d, ts);
    const art = $("[data-art]", stack), bg = $("[data-art-bg]", stack), src = art.getAttribute("src");
    bg.hidden = art.hidden;
    if (src && bg.getAttribute("src") !== src) bg.src = src;
  } else {
    paletteKey = null;
    applyPalette(skyPalette(conf.weather));
    if (id === "pc") {
      paintRings($("[data-pc]", stack), pcValues(d));
      const gpu = (d.gpu?.gpus || []).find(x => !x.integrated) || d.gpu?.gpus?.[0];
      $("[data-stats-pc]", stack).innerHTML = [d.cpu?.temp != null && cell("CPU", `${Math.round(d.cpu.temp)} °C`),
        gpu?.temp != null && cell("GPU", `${Math.round(gpu.temp)} °C`),
        gpu?.vram?.total && cell("VRAM", bytes(gpu.vram.used))].filter(Boolean).join("");
    }
  }
}

// ---------- eventos do PC ----------
async function alertEvents(a) {
  const seq = a?.seq ?? 0;
  if (lastSeq === null) { lastSeq = seq; return; }
  if (seq <= lastSeq) return;
  const from = lastSeq;
  lastSeq = seq;
  if (!conf.notifications) return;
  let list = [a];
  if (seq > from + 1) {          // o snapshot só traz o último: numa rajada, busca os demais
    try { list = (await app.api("/api/alerts")).alerts.filter(x => x.seq > from).reverse(); } catch { /* fica com o último */ }
  }
  list.forEach(x => deck.push(fromAlert(x, conf)));
}

function contextEvents(d) {
  const game = d.game?.game?.key ?? null, g = d.game?.game;
  if (lastGame !== undefined && game && game !== lastGame && conf.context) {
    deck.push({ id: `game-${game}`, kind: "takeover", level: "splash", title: g.name, eyebrow: "Agora jogando",
      art: artUrl(game, g.appid ? "cover" : undefined), seconds: SPLASH_SECONDS });
  }
  lastGame = game;

  const m = d.media?.current, track = m?.status === "playing" ? `${m.id}|${m.title}` : null;
  if (lastTrack !== undefined && track && track !== lastTrack && scene !== "music" && conf.context) {
    deck.push({ id: `track-${track}`, kind: "toast", level: "info", title: m.title, text: m.artist || m.app, icon: "music-note", art: artSrc(m), seconds: 5 });
  }
  lastTrack = track;
}

function paint(snap) {
  if (!conf) return;
  const d = snap.data, next = pick(d);
  if (next !== scene) enter(next);
  if (next === "game") paintGame(d);
  else if (next === "music") paintMusic(d, snap.ts);
  else if (next === "board") paintBoard(d, snap.ts);
  else if (next === "duo") paintDuo(d, snap.ts);
  else {
    const e = $("[data-empty]", el);
    if (e) e.textContent = emptyNote(d);
    paintRings($("[data-pc]", el), pcValues(d));     // Aura: o PC na cápsula do Início
    paintMini(d);
  }
  contextEvents(d);
  alertEvents(d.alerts?.latest);
}

// ---------- controles ----------
function wakeUp() {
  el.classList.add("awake");
  movePill();
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

async function nextLayout() {
  const [id, name] = LAYOUTS[(LAYOUTS.findIndex(([k]) => k === layout()) + 1) % LAYOUTS.length];
  conf = { ...conf, layout: id };
  scene = null;
  if (app.snapshot) paint(app.snapshot);
  deck.push({ id: `layout-${id}`, kind: "toast", level: "info", title: `Layout ${name}`, icon: "squares-four", seconds: 3 });
  try { conf = await app.api("/api/mural", { method: "POST", body: { layout: id } }); } catch { /* vale nesta tela até recarregar */ }
}

async function nextBackground() {
  const list = BACKDROPS.filter(([id]) => id !== "photo" || conf.background_v);
  const [id, name] = list[(list.findIndex(([k]) => k === conf.background) + 1) % list.length];
  conf = { ...conf, background: id };
  if (scene === "home" || scene === "board") paintHome();
  deck.push({ id: `bg-${id}`, kind: "toast", level: "info", title: name, icon: "image", seconds: 3 });
  try { conf = await app.api("/api/mural", { method: "POST", body: { background: id } }); } catch { /* vale nesta tela até recarregar */ }
}

async function refreshConf() {
  try {
    const before = layout();
    conf = await app.api("/api/mural");
    if (layout() !== before) { scene = null; if (app.snapshot) paint(app.snapshot); }
    paintConf();
    if (conf.location && !conf.weather && !conf.weather_error && !el.hidden) setTimeout(refreshConf, 3000);   // o clima ainda vai chegar
  } catch { /* tenta no próximo ciclo */ }
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
  el.innerHTML = `<canvas class="mural-canvas" aria-hidden="true"></canvas><img class="mural-bg" alt=""><img class="mural-bg" alt="">
    <div class="mural-mesh" aria-hidden="true"><i></i><i></i><i></i></div><div class="mural-scrim"></div>
    <div class="mural-scene"></div>
    <div class="mural-toasts" role="status" aria-live="polite"></div>
    <section class="mural-takeover" hidden></section>
    <nav class="mural-bar" aria-label="Controles do Mural">
      <div class="mural-dock-modes"><span class="mural-dock-pill"></span>
        ${MODES.map(([id, label, icon]) => `<button data-mode="${id}" aria-pressed="false" aria-label="${label}">${ic(icon)}<span>${label}</span></button>`).join("")}</div>
      <div class="mural-dock-tools">
        <button data-next-layout title="Trocar o layout" aria-label="Trocar o layout">${ic("squares-four")}</button>
        <button data-bg title="Trocar o fundo" aria-label="Trocar o fundo">${ic("image")}</button>
        <button data-fs title="Tela cheia" aria-label="Tela cheia">${ic("frame-corners")}</button>
        <button data-exit title="Sair do Mural" aria-label="Sair do Mural">${ic("x")}</button>
      </div>
    </nav>`;
  document.body.append(el);
  deck = new EventDeck(el);
  backdrop = new Backdrop($(".mural-canvas", el));
  document.fonts?.ready.then(movePill);             // a largura dos botões muda quando a fonte chega
  new ResizeObserver(movePill).observe($(".mural-dock-modes", el));
  el.addEventListener("input", e => { if (e.target.matches("[data-mvol-range]")) setVolume(Number(e.target.value)); });
  el.addEventListener("pointerdown", e => { if (e.target.matches("[data-mvol-range]")) vol.dragging = true; });
  el.addEventListener("pointerup", () => { if (vol.dragging) { vol.dragging = false; vol.at = Date.now(); } });
  el.addEventListener("pointermove", wakeUp);
  el.addEventListener("pointerdown", wakeUp);
  el.addEventListener("focusin", wakeUp);
  el.addEventListener("click", async e => {
    const mode = e.target.closest("[data-mode]");
    if (mode) return setMode(mode.dataset.mode);
    if (e.target.closest("[data-bg]")) return nextBackground();
    if (e.target.closest("[data-next-layout]")) return nextLayout();
    if (e.target.closest("[data-fs]")) return toggleFullscreen();
    if (e.target.closest("[data-exit]")) return close();
    if (e.target.closest("[data-pick-city]")) return close();
    if (e.target.closest("[data-mvol-mute]")) return toggleAppMute();
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
  if (deck.active) return deck.next();                             // primeiro dispensa o aviso
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
  scene = null; lastSeq = null; lastGame = undefined; lastTrack = undefined; paletteKey = null;
  clockTick();
  const tick = setInterval(clockTick, 1000);       // o relógio não espera a configuração chegar do PC
  await refreshConf();
  if (el.hidden) { clearInterval(tick); return; }
  if (app.snapshot) paint(app.snapshot);
  off = app.onSnapshot(paint);
  timers = [tick, setInterval(refreshConf, CONF_EVERY), setInterval(() => {
    const r = () => Math.round(Math.random() * 16 - 8);
    $(".mural-scene", el).style.translate = `${r()}px ${r()}px`;
    everyMinute();
  }, SHIFT_EVERY)];
  document.addEventListener("keydown", onKey);
  document.addEventListener("visibilitychange", keepAwake);
  keepAwake();
  wakeUp();
}

function stopLyrics() { clearInterval(lyr.timer); lyr = { key: null, data: null, m: null, ts: 0, recv: 0, timer: null, line: -2 }; }

export function close() {
  stopLyrics();
  if (!el || el.hidden) return;
  el.hidden = true;
  document.body.classList.remove("mural-open");
  off?.(); off = null;
  timers.forEach(clearInterval); timers = [];
  clearTimeout(idleTimer);
  deck.clear();
  backdrop.hide();
  covers = null;
  document.removeEventListener("keydown", onKey);
  document.removeEventListener("visibilitychange", keepAwake);
  if (document.fullscreenElement) document.exitFullscreen?.();
  wake?.release?.(); wake = null;
  onExit();
}
