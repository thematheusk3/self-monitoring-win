from __future__ import annotations

import psutil

from . import Collector, register
from .system import cpu_model
from ..hw.pdh import Query


@register
class Cpu(Collector):
    name = "cpu"
    order = 10

    def setup(self) -> None:
        psutil.cpu_percent(percpu=True)
        psutil.cpu_times_percent(interval=None)
        self.model = cpu_model()
        self.physical = psutil.cpu_count(logical=False)
        self.logical = psutil.cpu_count()
        # psutil.cpu_freq() no Windows devolve só o clock base; o clock real (com boost) vem de
        # "% Processor Performance" x clock base, como no Gerenciador de Tarefas
        self.q = Query()
        self.perf = self.q.add(r"\Processor Information(*)\% Processor Performance")
        self.base = self.q.add(r"\Processor Information(_Total)\Processor Frequency")
        self.q.collect()
        try:
            psutil.getloadavg()     # no Windows o psutil emula o load average numa thread
        except Exception:  # noqa: BLE001
            pass

    def info(self) -> dict:
        return {"model": self.model, "cores": self.physical, "threads": self.logical}

    def _freqs(self) -> tuple[list[int], float | None]:
        if not self.q.collect():
            return [], None
        base = self.base.value() if self.base else None
        if not base:
            return [], None
        per = self.perf.values() if self.perf else {}
        cores = sorted(((k, v) for k, v in per.items() if "_Total" not in k),
                       key=lambda kv: tuple(int(x) for x in kv[0].split(",") if x.isdigit()))
        return [round(base * v / 100) for _, v in cores], base

    def collect(self) -> dict:
        per = psutil.cpu_percent(percpu=True)
        total = sum(per) / len(per) if per else 0.0
        cur, base = self._freqs()
        fmax = None
        try:
            f = psutil.cpu_freq()
            fmax = f.max or None if f else None
        except Exception:  # noqa: BLE001
            pass
        temp, src = self.ctx.lhm.cpu_temp()
        times = psutil.cpu_times_percent(interval=None)
        self.record("cpu.total", round(total, 1))
        self.record("cpu.temp", temp)
        if cur:
            self.record("cpu.freq", sum(cur) / len(cur))
        try:
            load = psutil.getloadavg()
        except Exception:  # noqa: BLE001
            load = None
        return {
            "model": self.model,
            "cores": self.physical, "threads": self.logical,
            "total": round(total, 1),
            "per_core": per,
            "freq": {"avg": round(sum(cur) / len(cur)) if cur else None, "max": fmax, "base": base,
                     "per_core": cur},
            "temp": temp, "temp_source": src,
            "load": load,
            "breakdown": {"user": times.user, "system": times.system,
                          "interrupt": getattr(times, "interrupt", 0) + getattr(times, "dpc", 0)},
        }
