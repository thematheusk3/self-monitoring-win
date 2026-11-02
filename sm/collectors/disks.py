from __future__ import annotations

import time

import psutil

from . import Collector, register


@register
class Disks(Collector):
    name = "disks"
    order = 50

    def setup(self) -> None:
        self._prev = psutil.disk_io_counters(perdisk=True) or {}
        self._t = time.monotonic()

    def collect(self) -> dict:
        parts = []
        for p in psutil.disk_partitions(all=False):
            if not p.fstype or "cdrom" in p.opts:      # leitor de DVD vazio, unidade sem mídia
                continue
            try:
                u = psutil.disk_usage(p.mountpoint)
            except OSError:
                continue
            parts.append({"device": p.device.rstrip("\\"), "mount": p.mountpoint, "fs": p.fstype,
                          "total": u.total, "used": u.used, "percent": u.percent,
                          "removable": "removable" in p.opts})
        now = time.monotonic()
        cur = psutil.disk_io_counters(perdisk=True) or {}
        dt = max(now - self._t, 1e-3)
        io = []
        for name, c in cur.items():           # PhysicalDrive0, PhysicalDrive1...
            p = self._prev.get(name)
            if not p:
                continue
            io.append({"disk": name.replace("PhysicalDrive", "Disco "),
                       "read": max(0, c.read_bytes - p.read_bytes) / dt,
                       "write": max(0, c.write_bytes - p.write_bytes) / dt})
        self._prev, self._t = cur, now
        tr = sum(d["read"] for d in io)
        tw = sum(d["write"] for d in io)
        self.record("disks.read", tr)
        self.record("disks.write", tw)
        return {"partitions": parts, "io": io, "read": tr, "write": tw}
