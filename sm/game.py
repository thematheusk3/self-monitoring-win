"""Jogo atual + FPS via PresentMon.

Jogo aberto: a Steam grava o AppID do jogo em execução no registro (HKCU\\Software\\Valve\\Steam\\RunningAppID);
o processo do jogo é o que roda de dentro da pasta de instalação dele.

FPS: o Windows não tem um contador de FPS global; o PresentMon (ferramenta oficial da Intel, open source,
https://github.com/GameTechDev/PresentMon) lê os eventos de "present" do próprio Windows (ETW) e mede
qualquer jogo DirectX/Vulkan/OpenGL, sem injetar nada no jogo. Precisa de administrador (o painel roda assim).
Com a leitura ligada, o painel deixa o PresentMon rodando e lê a saída dele; desligada, ele é encerrado.

O jogo medido é o processo da janela em foco (se estiver desenhando quadros), senão o jogo da Steam,
senão o processo que mais desenha quadros.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import subprocess
import threading
import time
import winreg
from collections import deque
from pathlib import Path

import psutil

from .config import ROOT
from .desktop import STEAM_ROOTS
from .win import CREATE_NO_WINDOW

log = logging.getLogger(__name__)
STATE_FILE = ROOT / "data" / "game.json"
ACTIVE_WINDOW = 3.0     # s sem quadros = jogo fechado/minimizado
WINDOW_1PCT = 60.0      # s usados para o 1% low
FT_COLUMNS = ("FrameTime", "MsBetweenPresents", "msBetweenPresents")
# PresentMon 2.x: quanto do quadro a CPU e a GPU passaram trabalhando (ms) e a latência até a tela
CPU_COLUMNS = ("CPUBusy", "MsCPUBusy")
GPU_COLUMNS = ("GPUBusy", "MsGPUBusy")
LAT_COLUMNS = ("DisplayLatency", "MsDisplayLatency")
# processos que desenham quadros mas não são jogos
NOT_GAMES = {"dwm.exe", "explorer.exe", "chrome.exe", "msedge.exe", "firefox.exe", "discord.exe", "steamwebhelper.exe",
             "searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe", "textinputhost.exe",
             "applicationframehost.exe", "code.exe", "spotify.exe", "obs64.exe", "nvidia overlay.exe", "tabby.exe"}
MAX_NOT_GAMES = 200
MAX_RECENT = 8          # jogos vistos há pouco, para escolher o que não é jogo
RX_KEY = re.compile(r"(steam:\d{1,12}|exe:[a-z0-9._-]{1,80})")


def find_presentmon(configured: str = "") -> Path | None:
    """Só em pastas que exigem administrador para gravar: o painel roda elevado e vai executar o arquivo."""
    if configured:
        p = Path(configured)
        return p if p.is_file() else None
    winget = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "WinGet"
    cands = [winget / "Links" / "presentmon.exe"]
    for d in (ROOT / "tools", winget / "Packages"):
        if d.is_dir():
            cands += sorted(d.glob("**/PresentMon*.exe"), reverse=True)
    return next((p for p in cands if p.is_file()), None)


def running_steam_appid() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            v = int(winreg.QueryValueEx(k, "RunningAppID")[0])
    except (OSError, ValueError):
        return None
    return str(v) if v else None


class GameMonitor:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.lock = threading.Lock()
        self.exe = find_presentmon(ctx.config.presentmon)
        state = self._load()
        self.enabled = bool(state.get("fps_enabled"))
        saved = state.get("not_games")
        self.not_games: dict[str, str] = {str(k): str(v) for k, v in saved.items()} if isinstance(saved, dict) else {}
        self.recent: dict[str, str] = {}
        self.proc: subprocess.Popen | None = None
        self.frames: dict[int, deque] = {}     # pid -> (t, frametime ms)
        self.names: dict[int, str] = {}
        self.extra: dict[int, deque] = {}      # pid -> (t, frametime, cpu ocupada, gpu ocupada, latência) em ms
        self.error: str | None = None
        self._game_pid: tuple[str, int | None, float | None] | None = None   # (appid, pid, desde)
        if self.enabled:
            self._start()

    # ---------- ligar/desligar ----------
    def _load(self) -> dict:
        try:
            state = json.loads(STATE_FILE.read_text())
        except (OSError, ValueError):
            return {}
        return state if isinstance(state, dict) else {}

    def _save(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"fps_enabled": self.enabled, "not_games": self.not_games}))

    def set_not_game(self, key: str, name: str | None, ignored: bool) -> dict:
        """Marca (ou desmarca) um app como "não é jogo": ele some do jogo atual e do FPS."""
        if not valid_key(key):
            raise ValueError("Jogo inválido.")
        with self.lock:
            if not ignored:
                self.not_games.pop(key, None)
            elif key in self.not_games or len(self.not_games) < MAX_NOT_GAMES:
                self.not_games[key] = str(name or key.split(":", 1)[1])[:80]
            else:
                raise ValueError("A lista de apps que não são jogos está cheia.")
            self._save()
        return self.status()

    def set_enabled(self, on: bool) -> dict:
        if on and not self.exe:
            self.exe = find_presentmon(self.ctx.config.presentmon)    # pode ter sido instalado depois
        if on and not self.exe:
            raise RuntimeError("PresentMon não encontrado. No PC, num PowerShell como administrador: "
                               "winget install Intel.PresentMon.Console --scope machine  (ou rode o instalador "
                               "do self-monitoring com -PresentMon) e tente de novo.")
        self.enabled = on
        self._save()
        if on:
            self._start()
        else:
            self.stop()
        log.info("leitura de FPS %s", "ativada" if on else "desativada")
        return self.status()

    def _start(self) -> None:
        if not self.exe or (self.proc and self.proc.poll() is None):
            return
        threading.Thread(target=self._reader, daemon=True, name="presentmon").start()

    def stop(self) -> None:
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            p.terminate()
        with self.lock:
            self.frames.clear()
            self.extra.clear()

    def _reader(self) -> None:
        cmd = [str(self.exe), "--output_stdout", "--no_console_stats", "--stop_existing_session",
               "--session_name", "SelfMonitoring"]
        while self.enabled:
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                             encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
            except OSError as e:
                self.error = f"Não foi possível iniciar o PresentMon: {e}"
                return
            self.error = None
            header, ft_i = None, None
            for row in csv.reader(self.proc.stdout):
                if header is None:
                    if "ProcessID" in row:
                        header = row
                        pid_i, app_i = row.index("ProcessID"), row.index("Application")
                        ft_i = next((row.index(c) for c in FT_COLUMNS if c in row), None)
                        cpu_i, gpu_i, lat_i = (next((row.index(c) for c in cols if c in row), None)
                                               for cols in (CPU_COLUMNS, GPU_COLUMNS, LAT_COLUMNS))
                    continue
                if ft_i is None or len(row) != len(header):
                    continue
                try:
                    pid, ft = int(row[pid_i]), float(row[ft_i])
                except ValueError:
                    continue
                if ft <= 0:
                    continue
                now = time.time()
                with self.lock:
                    dq = self.frames.get(pid)
                    if dq is None:
                        dq = self.frames[pid] = deque(maxlen=40000)
                        self.names[pid] = row[app_i]
                    dq.append((now, ft))
                    extra = self.extra.get(pid)
                    if extra is None:
                        extra = self.extra[pid] = deque(maxlen=2000)
                    extra.append((now, ft, _num(row, cpu_i), _num(row, gpu_i), _num(row, lat_i)))
            err = (self.proc.stderr.read() if self.proc and self.proc.stderr else "").strip()
            if not self.enabled:
                return
            self.error = f"O PresentMon parou{f': {err[:200]}' if err else ''}"
            log.warning("presentmon: %s", self.error)
            time.sleep(10)        # tenta de novo

    # ---------- jogo atual ----------
    def running_game(self) -> dict | None:
        """Jogo da Steam em execução (registro) e o processo dele (dentro da pasta de instalação)."""
        appid = running_steam_appid()
        if not appid:
            self._game_pid = None
            return None
        if not self._game_pid or self._game_pid[0] != appid or \
                (self._game_pid[1] and not psutil.pid_exists(self._game_pid[1])):
            self._game_pid = (appid, *self._find_proc(appid))
        _, pid, since = self._game_pid
        name = steam_name(appid)
        if not name and int(appid) >= 2 ** 31:      # atalho de jogo não-Steam (AppId com o bit mais alto ligado)
            short = steam_shortcut(appid)
            if short and short.get("game_appid"):
                return {"appid": short["game_appid"], "name": short["game"], "since": since, "pid": pid, "source": "steam"}
            exe = Path(short["exe"]).name if short and short.get("exe") else None
            return {"appid": None, "name": (short or {}).get("name") or f"App {appid}", "since": since, "pid": pid,
                    "source": "steam", "exe": exe or f"app-{appid}"}
        return {"appid": appid, "name": name or f"App {appid}", "since": since, "pid": pid, "source": "steam"}

    def _game_from_exe(self, pid: int, pname: str | None) -> dict:
        """Jogo medido pelo PresentMon sem AppId da Steam. Se o executável está em steamapps\\common\\<pasta>
        de um jogo instalado (ex.: launcher de mod aberto por atalho não-Steam), vira esse jogo (nome e arte);
        senão usa o nome do atalho não-Steam com esse executável, o título da janela ou o nome do executável."""
        try:
            exe_path = psutil.Process(pid).exe()
        except (psutil.Error, OSError):
            exe_path = ""
        appid = _common_appid(exe_path)
        if appid:
            return {"appid": appid, "name": steam_name(appid) or f"App {appid}", "since": None, "pid": pid,
                    "source": "steam"}
        w = (self.ctx.data.get("focus") or {}).get("window") or {}
        name = shortcut_by_exe(exe_path) or (w.get("desktop") if w.get("pid") == pid else None)
        return {"appid": None, "name": name or (pname or "?").removesuffix(".exe"), "since": None,
                "pid": pid, "source": "presentmon", "exe": pname}

    def _find_proc(self, appid: str) -> tuple[int | None, float | None]:
        folder = steam_install_dir(appid)
        if not folder:
            return None, None
        prefix = str(folder).lower() + "\\"
        best = None
        for p in self.ctx.processes or []:
            try:
                exe = psutil.Process(p["pid"]).exe().lower() if p["name"].lower().endswith(".exe") else ""
            except (psutil.Error, OSError):
                continue
            if exe.startswith(prefix) and (best is None or (p["mem"] or 0) > (best["mem"] or 0)):
                best = p
        return (best["pid"], best["create_time"]) if best else (None, None)

    def tick(self) -> None:
        """Chamado pelo coletor: descarta quadros antigos e processos que pararam."""
        now = time.time()
        with self.lock:
            for pid in list(self.frames):
                dq = self.frames[pid]
                while dq and dq[0][0] < now - WINDOW_1PCT:
                    dq.popleft()
                if not dq or dq[-1][0] < now - 10:
                    del self.frames[pid]
                    self.names.pop(pid, None)
                    self.extra.pop(pid, None)

    def _is_game(self, name: str | None) -> bool:
        name = (name or "").lower()
        return name not in NOT_GAMES and game_key({"exe": name}) not in self.not_games

    def _target(self, game: dict | None, skip: int | None = None) -> int | None:
        now = time.time()
        live = {pid: dq for pid, dq in self.frames.items()
                if dq and dq[-1][0] >= now - ACTIVE_WINDOW and pid != skip and self._is_game(self.names.get(pid))}
        if not live:
            return None
        fg = ((self.ctx.data.get("focus") or {}).get("window") or {}).get("pid")
        if fg in live:
            return fg
        if game and game.get("pid") in live:
            return game["pid"]
        return max(live, key=lambda pid: sum(1 for t, _ in live[pid] if t >= now - 1))

    def status(self) -> dict:
        game = self.running_game()
        now = time.time()
        with self.lock:
            skip = None
            if game and game_key(game) in self.not_games:      # app da Steam que não é jogo
                skip, game = game.get("pid"), None
            pid = self._target(game, skip) if self.enabled else None
            dq = list(self.frames.get(pid, ())) if pid else []
            pname = self.names.get(pid) if pid else None
            ex = [e for e in self.extra.get(pid, ()) if e[0] >= now - 2.0] if pid else []
        recent = [ft for t, ft in dq if t >= now - 1.0]
        cpu_load, gpu_load = _busy(ex, 2), _busy(ex, 3)
        lats = [e[4] for e in ex if e[4] is not None and e[4] > 0]
        fps = round(1000 / (sum(recent) / len(recent)), 1) if recent else None
        fts = sorted(ft for _, ft in dq)
        low = None
        if len(fts) >= 20:
            p99 = fts[min(len(fts) - 1, int(len(fts) * 0.99))]
            low = round(1000 / p99, 1) if p99 else None
        live = fps is not None
        if live and (game is None or (game.get("pid") and game["pid"] != pid)):
            game = self._game_from_exe(pid, pname)
        with self.lock:
            if game:
                game = {**game, "key": game_key(game)}
                self.recent.pop(game["key"], None)
                self.recent[game["key"]] = game["name"]
                while len(self.recent) > MAX_RECENT:
                    self.recent.pop(next(iter(self.recent)))
            not_games = [{"key": k, "name": n} for k, n in self.not_games.items()]
            seen = [{"key": k, "name": n} for k, n in reversed(self.recent.items()) if k not in self.not_games]
        return {
            "enabled": self.enabled, "config_ok": (self.exe is not None) if self.enabled else None,
            "available": self.exe is not None, "conf_path": str(self.exe) if self.exe else None,
            "log_folder": None, "error": self.error if self.enabled else None,
            "game": game, "live": live,
            "fps": fps, "low1": low if live else None,
            "frametime": round(sum(recent) / len(recent), 2) if recent else None,
            "cpu_load": cpu_load if live else None, "gpu_load": gpu_load if live else None,
            "load_source": "presentmon" if live and (cpu_load is not None or gpu_load is not None) else None,
            "latency": round(sum(lats) / len(lats), 1) if live and lats else None,
            "log_file": pname, "not_games": not_games, "recent_games": seen,
        }


def _num(row: list[str], i: int | None) -> float | None:
    if i is None:
        return None
    try:
        v = float(row[i])
    except (ValueError, IndexError):
        return None
    return v if v == v else None     # NaN


def _busy(ex: list[tuple], col: int) -> float | None:
    """% do tempo de quadro em que a CPU/GPU trabalhou para o jogo (perto de 100% = é o gargalo)."""
    vals = [(e[1], e[col]) for e in ex if e[col] is not None]
    ft = sum(f for f, _ in vals)
    return round(min(100.0, 100 * sum(b for _, b in vals) / ft), 1) if vals and ft > 0 else None


def valid_key(key: str) -> bool:
    return bool(RX_KEY.fullmatch(key or ""))


def game_key(game: dict) -> str:
    """Identifica o jogo nas preferências do Mural (arte escolhida)."""
    if game.get("appid"):
        return f"steam:{game['appid']}"
    return "exe:" + (re.sub(r"[^a-z0-9._-]+", "-", (game.get("exe") or game.get("name") or "?").lower()).strip("-")[:80] or "x")


_names: dict[str, str | None] = {}


def _acf(appid: str) -> str | None:
    for root in STEAM_ROOTS:
        for lib in _libraries(root):
            try:
                return (lib / "steamapps" / f"appmanifest_{appid}.acf").read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return None


def steam_name(appid: str) -> str | None:
    if appid not in _names:
        m = re.search(r'"name"\s+"([^"]+)"', _acf(appid) or "")
        _names[appid] = m.group(1) if m else None
    return _names[appid]


NOT_GAME_NAMES = re.compile(r"redistributable|steamworks|proton |steam linux runtime|^steamvr$", re.I)


def steam_games() -> list[tuple[str, str]]:
    """(appid, nome) dos jogos instalados, em todas as bibliotecas da Steam."""
    out: dict[str, str] = {}
    for root in STEAM_ROOTS:
        for lib in _libraries(root):
            for acf in (lib / "steamapps").glob("appmanifest_*.acf"):
                appid = acf.stem.split("_", 1)[1]
                try:
                    m = re.search(r'"name"\s+"([^"]+)"', acf.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
                if m and not NOT_GAME_NAMES.search(m.group(1)):
                    out[appid] = m.group(1)
    return list(out.items())


def steam_install_dir(appid: str) -> Path | None:
    for root in STEAM_ROOTS:
        for lib in _libraries(root):
            try:
                text = (lib / "steamapps" / f"appmanifest_{appid}.acf").read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = re.search(r'"installdir"\s+"([^"]+)"', text)
            if m:
                return lib / "steamapps" / "common" / m.group(1)
    return None


RX_COMMON = re.compile(r"[\\/]steamapps[\\/]common[\\/]([^\\/]+)[\\/]", re.I)


def _installdir_appid(folder: str) -> str | None:
    """AppId do jogo instalado em steamapps\\common\\<folder> (ignora redistribuíveis e ferramentas)."""
    for root in STEAM_ROOTS:
        for lib in _libraries(root):
            for acf in (lib / "steamapps").glob("appmanifest_*.acf"):
                try:
                    text = acf.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                m = re.search(r'"installdir"\s+"([^"]+)"', text)
                n = re.search(r'"name"\s+"([^"]+)"', text)
                if m and m.group(1).lower() == folder.lower() and not (n and NOT_GAME_NAMES.search(n.group(1))):
                    return acf.stem.split("_", 1)[1]
    return None


_common_cache: dict[str, str | None] = {}


def _common_appid(exe_path: str) -> str | None:
    m = RX_COMMON.search(exe_path or "")
    if not m:
        return None
    folder = m.group(1).lower()
    if folder not in _common_cache:
        if len(_common_cache) > 64:
            _common_cache.clear()
        _common_cache[folder] = _installdir_appid(folder)
    return _common_cache[folder]


_shortcuts_cache: tuple[tuple, dict] = ((), {})
_shortcut_exes: dict[str, str] = {}


def steam_shortcuts() -> dict[str, str]:
    """AppId (uint32, como no RunningAppID) -> nome dos atalhos de jogos não-Steam (userdata\\*\\config\\shortcuts.vdf)."""
    global _shortcuts_cache
    files = []
    for root in STEAM_ROOTS:
        files += sorted((root / "userdata").glob("*/config/shortcuts.vdf"))
    stamp = []
    for f in files:
        try:
            stamp.append((str(f), f.stat().st_mtime))
        except OSError:
            pass
    stamp = tuple(stamp)
    if stamp == _shortcuts_cache[0]:
        return _shortcuts_cache[1]
    out: dict[str, str] = {}
    _shortcut_exes.clear()
    for path, _ in stamp:
        try:
            data, _ = parse_binary_vdf(Path(path).read_bytes()[:4 * 1024 * 1024])
        except (OSError, ValueError, IndexError):
            continue
        for entry in (data.get("shortcuts") or {}).values():
            if isinstance(entry, dict) and isinstance(entry.get("appid"), int) and entry.get("appname"):
                aid = str(entry["appid"] & 0xFFFFFFFF)
                out[aid] = str(entry["appname"])[:80]
                _shortcut_exes[aid] = str(entry.get("exe") or "").strip('"')[:500]
    _shortcuts_cache = (stamp, out)
    return out


def steam_shortcut(appid: str) -> dict | None:
    """Atalho não-Steam: {name, exe, game_appid?, game?}. game = jogo instalado em cuja pasta o exe está."""
    name = steam_shortcuts().get(appid)
    if not name:
        return None
    exe = _shortcut_exes.get(appid, "")
    out = {"name": name, "exe": exe}
    real = _common_appid(exe)
    if real:
        out["game_appid"], out["game"] = real, steam_name(real) or name
    return out


def shortcut_by_exe(exe_path: str) -> str | None:
    """Nome do atalho não-Steam que abre este executável."""
    if not exe_path:
        return None
    names = steam_shortcuts()
    target = os.path.normcase(os.path.normpath(exe_path))
    for aid, exe in list(_shortcut_exes.items()):
        if exe and os.path.normcase(os.path.normpath(exe)) == target:
            return names.get(aid)
    return None


def parse_binary_vdf(d: bytes, i: int = 0, depth: int = 0) -> tuple[dict, int]:
    """VDF binário da Steam (shortcuts.vdf). Chaves em minúsculas."""
    if depth > 16:
        raise ValueError("VDF aninhado demais")
    out: dict = {}
    while i < len(d):
        t = d[i]
        i += 1
        if t == 8:
            return out, i
        j = d.index(b"\0", i)
        key = d[i:j].decode(errors="replace").lower()
        i = j + 1
        if t == 0:
            out[key], i = parse_binary_vdf(d, i, depth + 1)
        elif t == 1:
            j = d.index(b"\0", i)
            out[key] = d[i:j].decode(errors="replace")
            i = j + 1
        elif t in (2, 3, 4, 6):
            out[key] = int.from_bytes(d[i:i + 4], "little")
            i += 4
        elif t == 7:
            out[key] = int.from_bytes(d[i:i + 8], "little")
            i += 8
        else:
            raise ValueError(f"tipo VDF desconhecido: {t}")
    return out, i


def _libraries(root: Path) -> list[Path]:
    libs = [root]
    try:
        vdf = (root / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        libs += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', vdf)]
    except OSError:
        pass
    return list(dict.fromkeys(libs))
