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
# processos que desenham quadros mas não são jogos
NOT_GAMES = {"dwm.exe", "explorer.exe", "chrome.exe", "msedge.exe", "firefox.exe", "discord.exe", "steamwebhelper.exe",
             "searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe", "textinputhost.exe",
             "applicationframehost.exe", "code.exe", "spotify.exe", "obs64.exe", "nvidia overlay.exe"}


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
        self.enabled = self._load()
        self.proc: subprocess.Popen | None = None
        self.frames: dict[int, deque] = {}     # pid -> (t, frametime ms)
        self.names: dict[int, str] = {}
        self.error: str | None = None
        self._game_pid: tuple[str, int | None, float | None] | None = None   # (appid, pid, desde)
        if self.enabled:
            self._start()

    # ---------- ligar/desligar ----------
    def _load(self) -> bool:
        try:
            return bool(json.loads(STATE_FILE.read_text()).get("fps_enabled"))
        except (OSError, ValueError):
            return False

    def set_enabled(self, on: bool) -> dict:
        if on and not self.exe:
            self.exe = find_presentmon(self.ctx.config.presentmon)    # pode ter sido instalado depois
        if on and not self.exe:
            raise RuntimeError("PresentMon não encontrado. No PC, num PowerShell como administrador: "
                               "winget install Intel.PresentMon.Console --scope machine  (ou rode o instalador "
                               "do self-monitoring com -PresentMon) e tente de novo.")
        self.enabled = on
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"fps_enabled": on}))
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
        return {"appid": appid, "name": steam_name(appid) or f"App {appid}", "since": since, "pid": pid, "source": "steam"}

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

    def _target(self, game: dict | None) -> int | None:
        now = time.time()
        live = {pid: dq for pid, dq in self.frames.items() if dq and dq[-1][0] >= now - ACTIVE_WINDOW}
        if not live:
            return None
        fg = ((self.ctx.data.get("focus") or {}).get("window") or {}).get("pid")
        if fg in live:
            return fg
        if game and game.get("pid") in live:
            return game["pid"]
        cands = [pid for pid in live if self.names.get(pid, "").lower() not in NOT_GAMES]
        return max(cands, key=lambda pid: sum(1 for t, _ in live[pid] if t >= now - 1), default=None)

    def status(self) -> dict:
        game = self.running_game()
        now = time.time()
        with self.lock:
            pid = self._target(game) if self.enabled else None
            dq = list(self.frames.get(pid, ())) if pid else []
            pname = self.names.get(pid) if pid else None
        recent = [ft for t, ft in dq if t >= now - 1.0]
        fps = round(1000 / (sum(recent) / len(recent)), 1) if recent else None
        fts = sorted(ft for _, ft in dq)
        low = None
        if len(fts) >= 20:
            p99 = fts[min(len(fts) - 1, int(len(fts) * 0.99))]
            low = round(1000 / p99, 1) if p99 else None
        live = fps is not None
        if live and (game is None or (game.get("pid") and game["pid"] != pid)):
            w = (self.ctx.data.get("focus") or {}).get("window") or {}
            name = w.get("desktop") if w.get("pid") == pid else None
            game = {"appid": None, "name": name or (pname or "?").removesuffix(".exe"), "since": None,
                    "pid": pid, "source": "presentmon", "exe": pname}
        if game:
            game = {**game, "key": game_key(game)}
        return {
            "enabled": self.enabled, "config_ok": (self.exe is not None) if self.enabled else None,
            "available": self.exe is not None, "conf_path": str(self.exe) if self.exe else None,
            "log_folder": None, "error": self.error if self.enabled else None,
            "game": game, "live": live,
            "fps": fps, "low1": low if live else None,
            "frametime": round(sum(recent) / len(recent), 2) if recent else None,
            "cpu_load": None, "gpu_load": None,
            "log_file": pname,
        }


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


def _libraries(root: Path) -> list[Path]:
    libs = [root]
    try:
        vdf = (root / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        libs += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', vdf)]
    except OSError:
        pass
    return list(dict.fromkeys(libs))
