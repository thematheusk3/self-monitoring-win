"""Histórico de processos: registra quem aparece e quem some.

O Windows não guarda histórico de processos encerrados (só com auditoria ligada ou Sysmon). Este
rastreador lista os processos a cada `track_interval` segundos (padrão 0,5 s) e, quando um PID some,
move o registro para a lista de encerrados. Processos que vivem menos que o intervalo podem escapar.

No Windows os PIDs são reaproveitados rápido: um PID conhecido com horário de início diferente é
tratado como um processo novo (o antigo terminou).

Os subprocessos do próprio monitor (PowerShell, PresentMon...) são ignorados.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import Counter, deque

import psutil

from .config import ROOT
from .win import HOME

log = logging.getLogger(__name__)
HIST_FILE = ROOT / "data" / "exited.json"
TEMP = os.environ.get("TEMP", str(HOME / "AppData" / "Local" / "Temp")).lower()
RISKY_DIRS = (TEMP + "\\", "c:\\windows\\temp\\", str(HOME / "Downloads").lower() + "\\",
              "c:\\users\\public\\", "c:\\programdata\\")


def real_path(exe: str | None) -> str | None:
    """Só caminhos de verdade: os pseudo-processos do Windows (Registry, MemCompression,
    Secure System) devolvem só o nome."""
    return exe if exe and (exe[1:3] == ":\\" or exe.startswith("\\\\")) else None


def short_user(u: str | None) -> str:
    return (u or "?").rsplit("\\", 1)[-1]


class Services:
    """PID -> nomes dos serviços do Windows que rodam nele (cache curto; montar a lista custa ~50 ms)."""

    def __init__(self) -> None:
        self.map: dict[int, list[str]] = {}
        self.ts = 0.0
        self.lock = threading.Lock()

    def get(self, pid: int) -> str | None:
        with self.lock:
            if time.time() - self.ts > 15 or (pid not in self.map and time.time() - self.ts > 2):
                m: dict[int, list[str]] = {}
                try:
                    for s in psutil.win_service_iter():
                        try:
                            d = s.as_dict()
                        except (psutil.Error, OSError):
                            continue
                        if d.get("pid"):
                            m.setdefault(d["pid"], []).append(d["name"])
                except (psutil.Error, OSError):
                    pass
                self.map, self.ts = m, time.time()
            names = self.map.get(pid)
        return ", ".join(names[:4]) + ("…" if len(names) > 4 else "") if names else None


SERVICES = Services()


def _risky(exe: str) -> bool:
    """Executáveis que costumam se apagar logo: guarda o hash enquanto ainda dá para ler."""
    return exe.lower().startswith(RISKY_DIRS)


def _quick_hash(path: str, max_bytes: int = 64 * 1024 * 1024) -> str | None:
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            n = 0
            for chunk in iter(lambda: f.read(1 << 20), b""):
                n += len(chunk)
                if n > max_bytes:
                    return None
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def capture(pid: int, services_pid: int | None) -> dict | None:
    """Lê o mínimo do processo rapidamente (ele pode sumir a qualquer momento)."""
    try:
        p = psutil.Process(pid)
        with p.oneshot():
            ct = p.create_time()
            name = p.name()
            ppid = p.ppid()
            try:
                argv = p.cmdline()
            except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
                argv = []
            try:
                exe = real_path(p.exe())
            except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
                exe = None
            try:
                user = short_user(p.username())
            except (psutil.AccessDenied, OSError):
                user = "?"
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None
    if pid in (0, 4):
        return None
    sha = _quick_hash(exe) if exe and _risky(exe) else None
    return {
        "id": f"{pid}-{int(ct * 1000)}",
        "sha256": sha,
        "pid": pid, "ppid": ppid, "name": name,
        "cmd": " ".join(argv)[:1000], "argv0": argv[0] if argv else None,
        "exe": exe, "exe_verified": exe is not None,
        "uid": None, "user": user,
        "start": round(ct, 2),
        "unit": SERVICES.get(pid) if services_pid and ppid == services_pid else None,
        "container": bool(exe and "\\windowsapps\\" in exe.lower()),
        "peak_cpu": 0.0, "peak_mem": 0,
    }


class ProcessTracker:
    def __init__(self, ctx) -> None:
        cfg = ctx.config
        self.interval = cfg.track_interval
        self.persist = cfg.track_persist
        self.self_pid = os.getpid()
        self.live: dict[int, dict] = {}
        self.ignored: set[int] = set()        # subprocessos do próprio painel (e os filhos deles)
        self.exited: deque[dict] = deque(maxlen=cfg.exited_max)
        self.total_exited = 0
        self.started_at = time.time()
        self.services_pid = next((p.pid for p in psutil.process_iter(["name"]) if p.info["name"] == "services.exe"), None)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._n = 0
        self._load()

    # ---------- persistência ----------
    def _load(self) -> None:
        if not self.persist:
            return
        try:
            data = json.loads(HIST_FILE.read_text(encoding="utf-8"))
            self.exited.extend(data.get("exited", [])[-self.exited.maxlen:])
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        if not self.persist:
            return
        try:
            with self._lock:
                data = {"exited": list(self.exited)}
            tmp = HIST_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            tmp.replace(HIST_FILE)
        except OSError as e:
            log.warning("não foi possível salvar o histórico de processos: %s", e)

    # ---------- laço ----------
    def start(self) -> None:
        self._scan(initial=True)
        threading.Thread(target=self._loop, daemon=True, name="proctrack").start()

    def stop(self) -> None:
        self._stop.set()
        self.save()

    def _loop(self) -> None:
        last_save = time.monotonic()
        while not self._stop.wait(self.interval):
            try:
                self._scan()
            except Exception:  # noqa: BLE001
                log.exception("proctrack: falha na varredura")
            if time.monotonic() - last_save > 300:
                self.save()
                last_save = time.monotonic()

    def _reused(self, known: set[int]) -> set[int]:
        """PIDs conhecidos que agora são outro processo (horário de início diferente)."""
        out = set()
        with self._lock:
            starts = {pid: self.live[pid]["start"] for pid in known if pid in self.live}
        for pid, start in starts.items():
            try:
                if abs(psutil.Process(pid).create_time() - start) > 0.5:
                    out.add(pid)
            except (psutil.Error, OSError):
                pass
        return out

    def _scan(self, initial: bool = False) -> None:
        now = time.time()
        self._n += 1
        pids = set(psutil.pids())
        with self._lock:
            known = set(self.live)
        both = known & pids
        reused = self._reused(both) if self._n % 2 == 0 else set()    # a cada ~1 s
        new = (pids - known) | reused
        gone = (known - pids) | reused
        recs = {}
        for pid in new:
            if pid in self.ignored and pid not in reused:
                continue
            rec = capture(pid, self.services_pid)
            if rec is None:
                continue
            if rec["ppid"] == self.self_pid or rec["ppid"] in self.ignored or pid == self.self_pid:
                self.ignored.add(pid)
                continue
            rec["preexisting"] = initial
            recs[pid] = rec
        self.ignored &= pids
        with self._lock:
            for pid in gone:
                if pid in self.live:
                    self._finish(self.live.pop(pid), now)
            for pid, rec in recs.items():
                parent = self.live.get(rec["ppid"])
                rec["parent_name"] = parent["name"] if parent else None
                self.live[pid] = rec

    def _finish(self, rec: dict, now: float) -> None:
        rec["end"] = round(now, 2)
        rec["duration"] = round(max(0.0, now - rec["start"]), 2)
        self.exited.append(rec)
        self.total_exited += 1

    def observe(self, procs: list[dict]) -> None:
        """Chamado pelo coletor de processos: guarda picos de CPU e memória."""
        with self._lock:
            for p in procs:
                rec = self.live.get(p["pid"])
                if rec is None:
                    continue
                if p["cpu"] > rec["peak_cpu"]:
                    rec["peak_cpu"] = p["cpu"]
                if p["mem"] > rec["peak_mem"]:
                    rec["peak_mem"] = p["mem"]

    # ---------- consultas ----------
    def get_live(self, pid: int) -> dict | None:
        with self._lock:
            r = self.live.get(pid)
            return dict(r) if r else None

    def find(self, rid: str) -> dict | None:
        with self._lock:
            for r in reversed(self.exited):
                if r["id"] == rid:
                    return dict(r)
            for r in self.live.values():
                if r["id"] == rid:
                    return dict(r)
        return None

    def ancestors(self, ppid: int, limit: int = 12) -> list[dict]:
        """Cadeia de pais (usa os registros vivos e encerrados)."""
        out = []
        with self._lock:
            by_pid = {r["pid"]: r for r in self.exited}
            by_pid.update(self.live)
            seen = set()
            while ppid and ppid not in seen and len(out) < limit:
                seen.add(ppid)
                r = by_pid.get(ppid)
                if not r:
                    out.append({"pid": ppid, "name": "?", "alive": False})
                    break
                out.append({"pid": r["pid"], "name": r["name"], "user": r["user"],
                            "alive": r["pid"] in self.live and "end" not in r})
                ppid = r["ppid"]
        return out

    def list_exited(self, limit: int = 2000) -> dict:
        with self._lock:
            items = list(self.exited)[-limit:]
            counts = Counter(r["name"] for r in self.exited)
        items.reverse()
        return {
            "exited": items,
            "total": self.total_exited,
            "kept": len(self.exited),
            "max": self.exited.maxlen,
            "interval": self.interval,
            "since": self.started_at,
            "top_names": counts.most_common(10),
        }

    def clear(self) -> None:
        with self._lock:
            self.exited.clear()
        self.save()
