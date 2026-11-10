"""Inventário de hardware e software base (placa, BIOS, CPU, GPU e driver, memória, discos, versão do Windows).

Gravado no banco quando muda (store.hardware): o histórico mostra quando uma peça, o BIOS, o driver de vídeo ou o
Windows mudou, o que ajuda a explicar incidentes ("começou depois da atualização do driver").
Coleta: uma consulta CIM (WMI) pelo PowerShell; roda na partida e a cada 6 h.
"""
from __future__ import annotations

import hashlib
import json
import logging
import platform
import re
import threading
import time
import winreg

from .win import powershell_json

log = logging.getLogger(__name__)
INTERVAL = 6 * 3600

PS = r"""
$b = Get-CimInstance Win32_BaseBoard; $bios = Get-CimInstance Win32_BIOS; $cs = Get-CimInstance Win32_ComputerSystem
$os = Get-CimInstance Win32_OperatingSystem
[pscustomobject]@{
  board = @{vendor=$b.Manufacturer; model=$b.Product; bios_vendor=$bios.Manufacturer; bios_version=$bios.SMBIOSBIOSVersion;
            bios_date=$(if ($bios.ReleaseDate) { $bios.ReleaseDate.ToString('yyyy-MM-dd') }); product=$cs.Model}
  cpu = @(Get-CimInstance Win32_Processor | ForEach-Object { @{model=$_.Name.Trim(); cores=$_.NumberOfCores; threads=$_.NumberOfLogicalProcessors} })
  memory = @{total=[int64]$cs.TotalPhysicalMemory; swap=[int64](@(Get-CimInstance Win32_PageFileUsage) | Measure-Object AllocatedBaseSize -Sum).Sum * 1MB}
  gpus = @(Get-CimInstance Win32_VideoController | ForEach-Object { @{name=$_.Name; driver=$_.DriverVersion;
            driver_date=$(if ($_.DriverDate) { $_.DriverDate.ToString('yyyy-MM-dd') }); pnp=$_.PNPDeviceID} })
  disks = @(Get-CimInstance Win32_DiskDrive | ForEach-Object { @{name=$_.DeviceID; model=$_.Model; size=[int64]$_.Size;
            transport=$_.InterfaceType; media=$_.MediaType; serial=$(if ($_.SerialNumber) { $_.SerialNumber.Trim() })} })
  os = $os.Caption; build = $os.BuildNumber
}
"""


def _ubr() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            build = winreg.QueryValueEx(k, "CurrentBuildNumber")[0]
            ubr = winreg.QueryValueEx(k, "UBR")[0]
            return f"{build}.{ubr}"
    except OSError:
        return None


def _microcode() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            v = winreg.QueryValueEx(k, "Update Revision")[0]
        if isinstance(v, bytes):
            return hex(int.from_bytes(v[4:8] or v, "little"))
        return str(v)
    except OSError:
        return None


def collect() -> dict:
    r = powershell_json(PS, timeout=60)
    d = r[0] if r else {}
    cpus = d.get("cpu") or [{}]
    cpu = {**(cpus[0] or {}), "microcode": _microcode()}
    disks = []
    for x in d.get("disks") or []:
        serial = x.pop("serial", None)
        # só o fim do número de série: identifica a troca de disco sem expor o número inteiro
        disks.append({**x, "serial_tail": serial[-4:] if serial else None})
    gpus = [{k: v for k, v in g.items() if k != "pnp"} | {"device_id": _pci_id(g.get("pnp"))} for g in d.get("gpus") or []]
    windows = _ubr() or d.get("build")
    packages = {"Windows": windows}
    for g in gpus:
        if g.get("name") and g.get("driver"):
            packages[f"Driver {g['name']}"] = g["driver"]
    return {"board": d.get("board") or {}, "cpu": cpu, "memory": d.get("memory") or {}, "gpus": gpus, "disks": disks,
            "kernel": windows or platform.version(), "os": d.get("os"), "packages": packages}


def _pci_id(pnp: str | None) -> str | None:
    m = re.search(r"VEN_([0-9A-F]{4})&DEV_([0-9A-F]{4})", pnp or "", re.I)
    return f"{m.group(1)}:{m.group(2)}".lower() if m else None


def fingerprint(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def diff(old: dict | None, new: dict, prefix: str = "") -> list[dict]:
    """Lista de mudanças [{path, old, new}] entre dois inventários."""
    if old is None:
        return []
    out = []
    keys = set(old) | set(new) if isinstance(old, dict) and isinstance(new, dict) else set()
    if not keys:
        if old != new:
            out.append({"path": prefix or "/", "old": old, "new": new})
        return out
    for k in sorted(keys, key=str):
        a, b = old.get(k), new.get(k)
        path = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(a, dict) and isinstance(b, dict):
            out += diff(a, b, path)
        elif a != b:
            out.append({"path": path, "old": a, "new": b})
    return out


class HardwareInventory:
    def __init__(self, store) -> None:
        self.store = store
        self._stop = threading.Event()

    def check(self) -> dict | None:
        """Coleta; grava se mudou. Devolve a linha nova (ou None se igual)."""
        data = collect()
        if not data.get("board") and not data.get("gpus"):
            return None                     # PowerShell falhou: não grava um inventário vazio
        h = fingerprint(data)
        last = self.store.hardware_latest()
        if last and last["hash"] == h:
            return None
        changes = diff(last["data"] if last else None, data)
        self.store.add_hardware(h, data, changes)
        if changes:
            log.info("hardware/software base mudou: %s", ", ".join(c["path"] for c in changes[:8]))
        return {"hash": h, "changes": changes}

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="hwinfo").start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        self._stop.wait(20)      # deixa a partida terminar
        while not self._stop.is_set():
            try:
                self.check()
            except Exception:  # noqa: BLE001
                log.exception("inventário de hardware falhou")
            self._stop.wait(INTERVAL)


def recent_upgrades(days: float = 7, now: float | None = None) -> list[dict]:
    """Atualizações instaladas pelo Windows Update nos últimos dias (inclui drivers e o Defender),
    lidas do log de eventos (WindowsUpdateClient 19 = instalada com sucesso)."""
    from . import eventlog
    now = now or time.time()
    xp = eventlog._xpath(["Provider[@Name='Microsoft-Windows-WindowsUpdateClient'] and EventID=19",
                          eventlog._time_cond(now - days * 86400, now)])
    out = []
    for m in eventlog._read("System", xp, 200, reverse=False, with_msg=False):
        title = (m.get("data") or {}).get("updateTitle") or m["msg"]
        if re.search(r"Security Intelligence|Inteligência de Segurança|KB2267602|^9[A-Z0-9]{11}-", title, re.I):
            continue        # definições do Defender e apps da Loja: várias por dia, não explicam travamentos
        kb = re.search(r"KB\d{6,8}", title)
        out.append({"ts": m["ts"], "action": "instalada", "package": title[:160], "version": kb.group(0) if kb else ""})
    return out
