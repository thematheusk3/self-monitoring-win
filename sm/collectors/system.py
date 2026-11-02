from __future__ import annotations

import platform
import socket
import time
import winreg

import psutil

from . import Collector, register


def _reg(path: str, name: str, root=winreg.HKEY_LOCAL_MACHINE) -> str | None:
    try:
        with winreg.OpenKey(root, path) as k:
            return str(winreg.QueryValueEx(k, name)[0]).strip()
    except OSError:
        return None


def os_name() -> str:
    """'Windows 11 Pro 24H2 (build 26100)'. O ProductName do registro diz "Windows 10" até no 11."""
    cv = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    build = _reg(cv, "CurrentBuildNumber") or platform.version().split(".")[-1]
    product = _reg(cv, "ProductName") or "Windows"
    if build.isdigit() and int(build) >= 22000:
        product = product.replace("Windows 10", "Windows 11")
    disp = _reg(cv, "DisplayVersion") or _reg(cv, "ReleaseId")
    return f"{product}{f' {disp}' if disp else ''} (build {build})"


def cpu_model() -> str:
    return _reg(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString") or platform.processor() or "CPU"


def _battery() -> dict | None:
    try:
        b = psutil.sensors_battery()
    except Exception:  # noqa: BLE001
        return None
    if b is None:
        return None
    status = "Carregando" if b.power_plugged and b.percent < 100 else "Na tomada" if b.power_plugged else "Na bateria"
    return {"percent": round(b.percent), "status": status}


@register
class System(Collector):
    name = "system"
    interval = 10
    order = 0

    def setup(self) -> None:
        bios = r"HARDWARE\DESCRIPTION\System\BIOS"
        self.static = {
            "hostname": socket.gethostname(),
            "os": os_name(),
            "kernel": platform.version(),
            "arch": platform.machine(),
            "cpu_model": cpu_model(),
            "board": _reg(bios, "BaseBoardProduct") or "",
            "board_vendor": _reg(bios, "BaseBoardManufacturer") or "",
            "boot_time": psutil.boot_time(),
        }

    def info(self) -> dict:
        return self.static

    def collect(self) -> dict:
        d = dict(self.static)
        d["uptime"] = time.time() - self.static["boot_time"]
        d["users"] = len(psutil.users())
        d["battery"] = _battery()
        d["lhm"] = self.ctx.lhm.ok
        return d
