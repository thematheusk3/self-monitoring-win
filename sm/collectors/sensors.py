from __future__ import annotations

import winreg

from . import Collector, register

FRIENDLY = {
    "amdcpu": "CPU (AMD)", "intelcpu": "CPU (Intel)", "lpc": "Placa-mãe", "gpu-nvidia": "GPU (NVIDIA)",
    "gpu-amd": "GPU (AMD)", "gpu-intel": "GPU (Intel)", "nvme": "SSD NVMe", "hdd": "Disco", "ssd": "SSD",
    "ram": "Memória", "battery": "Bateria", "psu": "Fonte", "cooler": "Cooler", "motherboard": "Placa-mãe",
}


def friendly(chip: str, device: str) -> str:
    base = FRIENDLY.get(chip)
    return f"{base} · {device}" if base and device else device or base or chip


def board_vendor() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS") as k:
            return str(winreg.QueryValueEx(k, "BaseBoardManufacturer")[0]).strip()
    except OSError:
        return ""


def fan_hint(chips: list[dict], fans: list[dict], vendor: str) -> str | None:
    """Por que nenhuma ventoinha aparece (o LibreHardwareMonitor responde, mas sem RPM)."""
    if fans:
        return None
    board = any(c["driver"] in ("lpc", "motherboard") for c in chips)
    who = f" da placa-mãe ({vendor})" if vendor else " da placa-mãe"
    if not board:
        return (f"Nenhuma ventoinha: o LibreHardwareMonitor não está lendo o chip de sensores{who}. Ele precisa rodar como "
                "administrador e, nas versões novas, do driver <b>PawnIO</b> (o instalador do LibreHardwareMonitor oferece; "
                "ou <code>winget install namazso.PawnIO</code>). Depois reinicie o LibreHardwareMonitor.")
    return (f"Nenhuma ventoinha: o chip{who} é lido, mas sem rotação. Confira se as ventoinhas estão nos conectores da placa "
            "(as ligadas num hub/controladora ou no cooler AIO com USB não aparecem para a placa) e se o LibreHardwareMonitor "
            "está atualizado.")


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
        if not hasattr(self, "_vendor"):
            self._vendor = board_vendor()
        hint = fan_hint(list(chips.values()), fans, self._vendor) if chips else None
        return {"chips": list(chips.values()), "fans": fans, "source": "lhm", "fan_hint": hint,
                "available": self.ctx.lhm.ok, "error": self.ctx.lhm.error, "url": self.ctx.lhm.url}
