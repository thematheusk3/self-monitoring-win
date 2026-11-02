from __future__ import annotations

from . import Collector, register
from ..hw import gpu as gpu_hw


@register
class Gpu(Collector):
    name = "gpu"
    order = 30

    def setup(self) -> None:
        self.gpus = gpu_hw.discover_all(self.ctx.gpu_engines)

    def info(self) -> dict:
        return {"gpus": [{"id": g.pci, "name": g.name, "vendor": g.vendor} for g in self.gpus]}

    def collect(self) -> dict:
        self.ctx.gpu_engines.update()     # também alimenta o uso de GPU por processo
        out = []
        for i, g in enumerate(self.gpus):
            try:
                s = g.sample(self.ctx.lhm)
            except Exception as e:  # noqa: BLE001
                s = {"id": g.pci, "name": g.name, "vendor": g.vendor, "error": str(e)}
            self.record(f"gpu.{i}.busy", s.get("busy"))
            self.record(f"gpu.{i}.temp", s.get("temp"))
            out.append(s)
        return {"gpus": out}
