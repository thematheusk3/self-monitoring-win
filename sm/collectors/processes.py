from __future__ import annotations

import os

import psutil

from . import Collector, register
from ..win import is_admin

ATTRS = ["pid", "ppid", "name", "username", "cpu_percent", "memory_info", "memory_percent",
         "status", "cmdline", "create_time", "num_threads"]
SYSTEM_PIDS = (0, 4)       # System Idle Process, System (núcleo do Windows)


def short_user(u: str | None) -> str:
    """'DESKTOP-X\\usuario' -> 'usuario'; 'NT AUTHORITY\\SYSTEM' -> 'SYSTEM'."""
    return (u or "?").rsplit("\\", 1)[-1]


@register
class Processes(Collector):
    """Lista completa fica em ctx.processes (servida por /api/processes);
    o snapshot leva só um resumo dos maiores consumidores."""
    name = "processes"
    order = 90

    def setup(self) -> None:
        self.ncpu = psutil.cpu_count() or 1
        self.self_pid = os.getpid()
        self.interval = self.ctx.config.process_interval
        self.admin = is_admin()
        list(psutil.process_iter(ATTRS))  # inicializa cpu_percent

    def collect(self) -> dict:
        gpu = self.ctx.gpu_engines.per_pid
        procs = []
        for p in psutil.process_iter(ATTRS):
            i = p.info
            if i["pid"] == 0:          # "System Idle Process": é o tempo ocioso, não um processo
                continue
            mi = i.get("memory_info")
            cmd = i.get("cmdline") or []
            g = gpu.get(i["pid"], {})
            procs.append({
                "pid": i["pid"], "ppid": i.get("ppid"), "name": i.get("name") or "?",
                "user": short_user(i.get("username")),
                "cpu": round((i.get("cpu_percent") or 0) / self.ncpu, 1),
                "mem": mi.rss if mi else 0,
                "mem_pct": round(i.get("memory_percent") or 0, 1),
                "status": i.get("status"),
                "cmd": " ".join(cmd)[:300],
                "threads": i.get("num_threads"),
                "create_time": i.get("create_time"),
                "gpu": g.get("gpu"), "gpu_mem": g.get("gpu_mem"),
                "kernel": i["pid"] in SYSTEM_PIDS or (i.get("name") in ("Registry", "Secure System", "Memory Compression")
                                                     and i.get("ppid") == 4),
                "self": i["pid"] == self.self_pid,
            })
        self.ctx.processes = procs
        if self.ctx.proctrack:
            self.ctx.proctrack.observe(procs)
        user = [p for p in procs if not p["kernel"]]
        top = lambda key, n=5: [  # noqa: E731
            {k: p[k] for k in ("pid", "name", "cpu", "mem", "gpu")}
            for p in sorted(user, key=lambda p: p[key] or 0, reverse=True)[:n] if (p[key] or 0) > 0
        ]
        return {
            "count": len(procs),
            "threads": sum(p["threads"] or 0 for p in procs),
            "top_cpu": top("cpu"), "top_mem": top("mem"), "top_gpu": top("gpu"),
            # os contadores de GPU do Windows enxergam todos os processos, com ou sem admin
            "gpu_visibility": "all",
            "admin": self.admin,
        }
