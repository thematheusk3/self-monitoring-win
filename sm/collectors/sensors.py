from __future__ import annotations

from . import Collector, register

FRIENDLY = {
    "amdcpu": "CPU (AMD)", "intelcpu": "CPU (Intel)", "lpc": "Placa-mãe", "gpu-nvidia": "GPU (NVIDIA)",
    "gpu-amd": "GPU (AMD)", "gpu-intel": "GPU (Intel)", "nvme": "SSD NVMe", "hdd": "Disco", "ssd": "SSD",
    "ram": "Memória", "battery": "Bateria", "psu": "Fonte", "cooler": "Cooler", "motherboard": "Placa-mãe",
}


def friendly(chip: str, device: str) -> str:
    base = FRIENDLY.get(chip)
    return f"{base} · {device}" if base and device else device or base or chip


@register
class Sensors(Collector):
    """Sensores do LibreHardwareMonitor, agrupados por hardware."""
    name = "sensors"
    order = 40

    def collect(self) -> dict:
        chips: dict[str, dict] = {}
        for r in self.ctx.lhm.readings():
            c = chips.setdefault(r.chip_id, {
                "id": r.chip_id, "driver": r.chip, "name": friendly(r.chip, r.device),
                "device": "", "readings": [],
            })
            c["readings"].append(r.as_dict())
        fans = [{"chip": c["name"], "label": r["label"], "rpm": r["value"]}
                for c in chips.values() for r in c["readings"] if r["kind"] == "fan"]
        return {"chips": list(chips.values()), "fans": fans, "source": "lhm",
                "available": self.ctx.lhm.ok, "error": self.ctx.lhm.error, "url": self.ctx.lhm.url}
