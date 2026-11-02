"""Sensores via LibreHardwareMonitor (temperaturas, ventoinhas, tensões, potência).

O Windows não expõe os sensores da placa-mãe/CPU para programas comuns; quem lê é um driver.
O LibreHardwareMonitor (gratuito, open source) faz isso e publica tudo no próprio servidor web
(Options > Remote Web Server > Run, porta 8085). O painel lê http://127.0.0.1:8085/data.json.

Sem o LibreHardwareMonitor rodando, o painel continua funcionando, só sem esses sensores
(a temperatura da GPU NVIDIA vem do driver, independente dele).
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from dataclasses import dataclass

# grupo do LHM -> (tipo, unidade, faixa plausível)
GROUPS = {
    "Temperatures": ("temp", "°C", (1, 150)),
    "Fans": ("fan", "RPM", (0, 20000)),
    "Voltages": ("in", "V", (0.05, 60)),
    "Powers": ("power", "W", (0.05, 1500)),
    "Currents": ("curr", "A", (0.01, 300)),
    "Clocks": ("freq", "MHz", (1, 10000)),
    "Controls": ("control", "%", (0, 100)),
}
_NUM = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
CPU_TEMP_LABELS = ("Core (Tctl/Tdie)", "CPU (Tctl/Tdie)", "Tctl/Tdie", "Tctl", "Tdie", "CPU Package", "Package",
                   "Core Average", "Core (Tctl)")


@dataclass
class Reading:
    chip: str        # tipo do hardware no LHM (amdcpu, lpc, gpu-nvidia, nvme...)
    chip_id: str     # identificador único (/amdcpu/0)
    device: str      # nome do hardware (AMD Ryzen 7 9800X3D, Nuvoton NCT6799D...)
    kind: str
    label: str
    value: float
    unit: str
    crit: float | None = None

    def as_dict(self) -> dict:
        return {"chip": self.chip, "chip_id": self.chip_id, "device": self.device, "kind": self.kind,
                "label": self.label, "value": round(self.value, 2), "unit": self.unit, "crit": self.crit}


def parse_value(s: str | None) -> float | None:
    if not s:
        return None
    m = _NUM.search(s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def parse(tree: dict) -> list[Reading]:
    """Árvore do data.json -> leituras. Formato: computador > hardware > (sub-hardware) > grupo > sensor."""
    out: list[Reading] = []

    def walk(node: dict, hw: dict | None) -> None:
        kids = node.get("Children") or []
        text = node.get("Text") or ""
        if text in GROUPS and hw is not None:
            kind, unit, (lo, hi) = GROUPS[text]
            for s in kids:
                v = parse_value(s.get("Value"))
                if v is None or not lo <= abs(v) <= hi:
                    continue
                sid = s.get("SensorId") or ""
                parts = [p for p in sid.split("/") if p]
                chip = parts[0] if parts else hw["type"]
                chip_id = "/" + "/".join(parts[:-2]) if len(parts) > 2 else hw["id"]
                out.append(Reading(chip, chip_id, hw["name"], kind, s.get("Text") or "?", v, unit))
            return
        img = node.get("ImageURL") or ""
        if "images_icon/" in img and kids:   # nó de hardware (cpu.png, mainboard.png, nvidia.png, hdd.png...)
            hw = {"name": text, "type": img.rsplit("/", 1)[-1].removesuffix(".png"), "id": f"{text}#{node.get('id')}"}
        for k in kids:
            walk(k, hw)

    walk(tree, None)
    return out


class LHM:
    """Leitor com cache curto (vários coletores pedem no mesmo segundo)."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.lock = threading.Lock()
        self._cache: tuple[float, list[Reading]] = (0.0, [])
        self.ok = False
        self.error: str | None = None
        self._retry_at = 0.0

    def readings(self) -> list[Reading]:
        now = time.monotonic()
        with self.lock:
            if now - self._cache[0] < 0.8:
                return self._cache[1]
            if not self.ok and now < self._retry_at:   # fora do ar: tenta de novo a cada 15 s
                return []
            try:
                with urllib.request.urlopen(self.url, timeout=0.7) as r:
                    data = parse(json.loads(r.read().decode("utf-8", "replace")))
                self.ok, self.error = True, None
            except (OSError, ValueError) as e:
                data = []
                self.ok, self.error = False, str(e)
                self._retry_at = now + 15
            self._cache = (now, data)
            return data

    def cpu_temp(self) -> tuple[float | None, str | None]:
        temps = [r for r in self.readings() if r.kind == "temp" and r.chip in ("amdcpu", "intelcpu", "cpu")]
        for lab in CPU_TEMP_LABELS:
            for r in temps:
                if r.label == lab:
                    return r.value, f"LHM/{r.label}"
        if temps:
            return temps[0].value, f"LHM/{temps[0].label}"
        return None, None

    def gpu_temps(self, name: str) -> dict[str, float]:
        """Temperaturas da GPU com esse nome (para AMD/Intel, que o Windows não informa)."""
        norm = lambda s: re.sub(r"\W+", "", s.lower())  # noqa: E731
        key = norm(name)
        out = {}
        for r in self.readings():
            dev = norm(r.device)
            if r.kind == "temp" and r.chip.startswith("gpu") and dev and (dev in key or key in dev):
                out[r.label] = r.value
        return out
