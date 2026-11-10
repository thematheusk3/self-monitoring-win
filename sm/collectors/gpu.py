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
            for name in ("vram", "gtt"):          # % da memória da GPU (vazamento/estouro aparece nos incidentes)
                m = s.get(name) or {}
                if m.get("total") and m.get("used") is not None:
                    self.record(f"gpu.{i}.{name}", 100 * m["used"] / m["total"])
            if isinstance(s.get("power"), (int, float)):
                self.record(f"gpu.{i}.power", s["power"])
            clocks = s.get("clocks") or {}
            clk = clocks.get("core") or clocks.get("graphics") or clocks.get("sclk")
            if isinstance(clk, (int, float)):
                self.record(f"gpu.{i}.clock", clk)
            out.append(s)
        return {"gpus": out}
