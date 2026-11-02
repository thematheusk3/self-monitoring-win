"""Tela: resolução/taxa de atualização, brilho/contraste do monitor (DDC/CI) e espera.

- Resolução: ChangeDisplaySettingsEx (Win32). Toda mudança precisa ser confirmada em REVERT_AFTER segundos;
  sem confirmação volta sozinha ao modo anterior (evita ficar com tela preta sem poder desfazer).
- Brilho/contraste: DDC/CI pela dxva2.dll (fala direto com o monitor). Brilho e contraste em 0 deixam a
  tela praticamente apagada SEM desligar o monitor; o PC continua vendo o monitor conectado.
- Espera: SC_MONITORPOWER desliga a imagem; qualquer tecla/mouse acorda. Alguns monitores DisplayPort
  "somem" do Windows em espera profunda; por isso o brilho zero é a opção recomendada.
"""
from __future__ import annotations

import ctypes
import json
import re
import threading
import time
from ctypes import wintypes

from .config import ROOT

REVERT_AFTER = 20
VCP_BRIGHTNESS, VCP_CONTRAST = 0x10, 0x12
user32 = ctypes.windll.user32


class DisplayError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# ---------- estruturas Win32 ----------
class DEVMODEW(ctypes.Structure):
    _fields_ = [("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD),
                ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD), ("dmDriverExtra", wintypes.WORD),
                ("dmFields", wintypes.DWORD), ("dmPositionX", wintypes.LONG), ("dmPositionY", wintypes.LONG),
                ("dmDisplayOrientation", wintypes.DWORD), ("dmDisplayFixedOutput", wintypes.DWORD),
                ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short), ("dmYResolution", ctypes.c_short),
                ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
                ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD), ("dmPelsWidth", wintypes.DWORD),
                ("dmPelsHeight", wintypes.DWORD), ("dmDisplayFlags", wintypes.DWORD),
                ("dmDisplayFrequency", wintypes.DWORD), ("dmICMMethod", wintypes.DWORD),
                ("dmICMIntent", wintypes.DWORD), ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD),
                ("dmReserved1", wintypes.DWORD), ("dmReserved2", wintypes.DWORD),
                ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD)]


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32), ("DeviceString", wintypes.WCHAR * 128),
                ("StateFlags", wintypes.DWORD), ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


ENUM_CURRENT_SETTINGS = -1
DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x1
DISPLAY_DEVICE_PRIMARY_DEVICE = 0x4
DM_PELSWIDTH, DM_PELSHEIGHT, DM_DISPLAYFREQUENCY = 0x80000, 0x100000, 0x400000
CDS_UPDATEREGISTRY = 0x1
DISP_CHANGE = {0: None, 1: "é preciso reiniciar o PC", -1: "o driver recusou", -2: "modo não suportado",
               -3: "não foi possível gravar no registro", -4: "parâmetros inválidos", -5: "parâmetros inválidos",
               -6: "falha do adaptador"}
OUTPUT_TECH = {5: "HDMI", 10: "DisplayPort", 11: "DisplayPort", 4: "DVI", 0: "VGA", 0x80000000: "Interna"}


# ---------- nomes amigáveis (DisplayConfig) ----------
class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _PATH(ctypes.Structure):
    _fields_ = [("srcAdapter", _LUID), ("srcId", ctypes.c_uint32), ("srcModeIdx", ctypes.c_uint32),
                ("srcFlags", ctypes.c_uint32), ("tgtAdapter", _LUID), ("tgtId", ctypes.c_uint32),
                ("tgtModeIdx", ctypes.c_uint32), ("outputTechnology", ctypes.c_uint32), ("rotation", ctypes.c_uint32),
                ("scaling", ctypes.c_uint32), ("refreshNum", ctypes.c_uint32), ("refreshDen", ctypes.c_uint32),
                ("scanLine", ctypes.c_uint32), ("available", wintypes.BOOL), ("tgtFlags", ctypes.c_uint32),
                ("flags", ctypes.c_uint32)]


class _HEADER(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("size", ctypes.c_uint32), ("adapterId", _LUID), ("id", ctypes.c_uint32)]


class _SOURCE_NAME(ctypes.Structure):
    _fields_ = [("header", _HEADER), ("viewGdiDeviceName", wintypes.WCHAR * 32)]


class _TARGET_NAME(ctypes.Structure):
    _fields_ = [("header", _HEADER), ("flags", ctypes.c_uint32), ("outputTechnology", ctypes.c_uint32),
                ("edidManufactureId", ctypes.c_uint16), ("edidProductCodeId", ctypes.c_uint16),
                ("connectorInstance", ctypes.c_uint32), ("monitorFriendlyDeviceName", wintypes.WCHAR * 64),
                ("monitorDevicePath", wintypes.WCHAR * 128)]


def friendly_names() -> dict[str, dict]:
    """'\\\\.\\DISPLAY1' -> {"name": "LG ULTRAGEAR", "connector": "DisplayPort"}"""
    np, nm = ctypes.c_uint32(), ctypes.c_uint32()
    if user32.GetDisplayConfigBufferSizes(2, ctypes.byref(np), ctypes.byref(nm)):   # QDC_ONLY_ACTIVE_PATHS
        return {}
    paths = (_PATH * np.value)()
    modes = (ctypes.c_byte * (64 * max(1, nm.value)))()
    if user32.QueryDisplayConfig(2, ctypes.byref(np), paths, ctypes.byref(nm), modes, None):
        return {}
    out = {}
    for p in paths[:np.value]:
        src = _SOURCE_NAME()
        src.header = _HEADER(1, ctypes.sizeof(_SOURCE_NAME), p.srcAdapter, p.srcId)
        tgt = _TARGET_NAME()
        tgt.header = _HEADER(2, ctypes.sizeof(_TARGET_NAME), p.tgtAdapter, p.tgtId)
        if user32.DisplayConfigGetDeviceInfo(ctypes.byref(src)) or user32.DisplayConfigGetDeviceInfo(ctypes.byref(tgt)):
            continue
        out[src.viewGdiDeviceName] = {"name": tgt.monitorFriendlyDeviceName or None,
                                      "connector": OUTPUT_TECH.get(tgt.outputTechnology)}
    return out


def _monitors() -> list[tuple[int, str]]:
    """[(HMONITOR, '\\\\.\\DISPLAY1')]"""
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    def cb(hmon, _hdc, _rc, _):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            out.append((hmon, mi.szDevice))
        return True
    user32.EnumDisplayMonitors(None, None, cb, 0)
    return out


def _scale(hmon) -> float | None:
    x, y = wintypes.UINT(), wintypes.UINT()
    try:
        if ctypes.windll.shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(x), ctypes.byref(y)) == 0:
            return round(x.value / 96, 2)
    except (OSError, AttributeError):
        pass
    return None


def _devmode(dev: str, i: int) -> DEVMODEW | None:
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    return dm if user32.EnumDisplaySettingsExW(dev, i, ctypes.byref(dm), 0) else None


def mode_id(w: int, h: int, hz: int) -> str:
    return f"{w}x{h}@{hz}"


def outputs() -> list[dict]:
    names = friendly_names()
    scales = {dev: _scale(h) for h, dev in _monitors()}
    out = []
    i = 0
    while True:
        dd = DISPLAY_DEVICEW()
        dd.cb = ctypes.sizeof(DISPLAY_DEVICEW)
        if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            break
        i += 1
        if not dd.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
            continue
        dev = dd.DeviceName
        cur = _devmode(dev, ENUM_CURRENT_SETTINGS)
        seen, modes = set(), []
        j = 0
        while (dm := _devmode(dev, j)) is not None:
            j += 1
            if dm.dmBitsPerPel != 32 or dm.dmDisplayFrequency <= 1:
                continue
            key = (dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency)
            if key not in seen:
                seen.add(key)
                modes.append({"id": mode_id(*key), "w": key[0], "h": key[1], "hz": key[2]})
        modes.sort(key=lambda m: (-m["w"] * m["h"], -m["hz"]))
        current = None
        if cur:
            cid = mode_id(cur.dmPelsWidth, cur.dmPelsHeight, cur.dmDisplayFrequency)
            current = next((m for m in modes if m["id"] == cid), None) or \
                {"id": cid, "w": cur.dmPelsWidth, "h": cur.dmPelsHeight, "hz": cur.dmDisplayFrequency}
        fn = names.get(dev, {})
        top = max(modes, key=lambda m: (m["w"] * m["h"], m["hz"])) if modes else None
        out.append({"name": dev.removeprefix("\\\\.\\"), "label": fn.get("name") or dd.DeviceString,
                    "connector": fn.get("connector"), "enabled": True, "current": current, "modes": modes,
                    "scale": scales.get(dev), "vrr": None, "hdr": None,
                    "primary": bool(dd.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE),
                    "preferred": [top["id"]] if top else []})
    return out


def _apply(output: str, mid: str) -> None:
    m = re.fullmatch(r"(\d{3,5})x(\d{3,5})@(\d{2,3})", mid)
    if not m:
        raise DisplayError("Modo inválido.")
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency = (int(x) for x in m.groups())
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY
    rc = user32.ChangeDisplaySettingsExW("\\\\.\\" + output, ctypes.byref(dm), None, CDS_UPDATEREGISTRY, None)
    if rc:
        raise DisplayError(f"O Windows recusou o modo: {DISP_CHANGE.get(rc, rc)}.", 500)


class ModeChanger:
    """Aplica um modo e reverte se não for confirmado a tempo."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pending: dict | None = None   # {output, previous, new, deadline}

    def status(self) -> dict | None:
        with self.lock:
            if not self.pending:
                return None
            return {**self.pending, "seconds_left": max(0, round(self.pending["deadline"] - time.time()))}

    def apply(self, output: str, mid: str) -> dict:
        if not re.fullmatch(r"DISPLAY\d{1,2}", output or "") or not re.fullmatch(r"\d{3,5}x\d{3,5}@\d{2,3}", str(mid)):
            raise DisplayError("Saída ou modo inválido.")
        o = next((o for o in outputs() if o["name"] == output), None)
        if not o:
            raise DisplayError("Monitor não encontrado.", 404)
        mode = next((m for m in o["modes"] if m["id"] == mid), None)
        if not mode:
            raise DisplayError("Modo não suportado por este monitor.")
        with self.lock:
            if self.pending:
                raise DisplayError("Confirme ou desfaça a mudança anterior primeiro.", 409)
            prev = o["current"]["id"] if o["current"] else None
            _apply(output, mid)
            self.pending = {"output": output, "previous": prev, "new": mid,
                            "label": f"{mode['w']}x{mode['h']} @ {mode['hz']} Hz",
                            "deadline": time.time() + REVERT_AFTER}
            token = self.pending
        threading.Timer(REVERT_AFTER, self._timeout, args=(token,)).start()
        return self.status()

    def confirm(self) -> None:
        with self.lock:
            self.pending = None

    def revert(self) -> None:
        with self.lock:
            p, self.pending = self.pending, None
        if p and p["previous"]:
            _apply(p["output"], p["previous"])

    def _timeout(self, token: dict) -> None:
        with self.lock:
            if self.pending is not token:   # já confirmado/desfeito
                return
        try:
            self.revert()
        except DisplayError:
            pass


MODES = ModeChanger()


def dpms(on: bool) -> None:
    if on:   # um movimento mínimo do mouse acorda o monitor
        user32.mouse_event(0x0001, 0, 1, 0, 0)
        user32.mouse_event(0x0001, 0, -1, 0, 0)
    else:    # PostMessage: o SendMessage para todas as janelas pode travar esperando uma que não responde
        user32.PostMessageW(0xFFFF, 0x0112, 0xF170, 2)    # HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, desligar


# ---------- DDC/CI ----------
class PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [("hPhysicalMonitor", wintypes.HANDLE), ("szPhysicalMonitorDescription", wintypes.WCHAR * 128)]


_ddc_lock = threading.Lock()
_ddc_cache: dict = {"ts": 0, "data": None}
SAVED_FILE = ROOT / "data" / "display-dim.json"   # valores antes de "apagar", para restaurar


def _load_saved() -> dict:
    try:
        return {int(k): v for k, v in json.loads(SAVED_FILE.read_text()).items()}
    except (OSError, ValueError):
        return {}


def _store_saved(d: dict) -> None:
    try:
        SAVED_FILE.write_text(json.dumps(d))
    except OSError:
        pass


_saved: dict = _load_saved()


def _physical(fn):
    """Abre os monitores físicos, chama fn([(índice, handle, descrição, dispositivo)]) e libera os handles."""
    dxva = ctypes.windll.dxva2
    opened, items = [], []
    try:
        for hmon, dev in _monitors():
            n = wintypes.DWORD()
            if not dxva.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(n)) or not n.value:
                continue
            arr = (PHYSICAL_MONITOR * n.value)()
            if not dxva.GetPhysicalMonitorsFromHMONITOR(hmon, n.value, arr):
                continue
            opened.append((n.value, arr))
            for pm in arr:
                items.append((len(items), pm.hPhysicalMonitor, pm.szPhysicalMonitorDescription, dev))
        return fn(items)
    finally:
        for n, arr in opened:
            dxva.DestroyPhysicalMonitors(n, arr)


def _get_vcp(h, code: int) -> tuple[int, int] | None:
    cur, mx = wintypes.DWORD(), wintypes.DWORD()
    for _ in range(2):           # DDC às vezes falha na primeira tentativa
        if ctypes.windll.dxva2.GetVCPFeatureAndVCPFeatureReply(h, code, None, ctypes.byref(cur), ctypes.byref(mx)):
            return cur.value, mx.value
        time.sleep(0.05)
    return None


def ddc_monitors(refresh: bool = False) -> list[dict]:
    """Monitores com DDC/CI: [{bus, model, brightness, contrast}] (cache de 30 s: o DDC é lento)."""
    with _ddc_lock:
        if not refresh and _ddc_cache["data"] is not None and time.time() - _ddc_cache["ts"] < 30:
            return _ddc_cache["data"]
        names = friendly_names()

        def read(items):
            mons = []
            for idx, h, desc, dev in items:
                b, c = _get_vcp(h, VCP_BRIGHTNESS), _get_vcp(h, VCP_CONTRAST)
                if b is None and c is None:      # monitor sem DDC/CI (ou desligado no menu dele)
                    continue
                fn = names.get(dev, {})
                mons.append({"bus": idx, "model": fn.get("name") or desc or "Monitor",
                             "connector": dev.removeprefix("\\\\.\\"),
                             "brightness": b[0] if b else None, "brightness_max": b[1] if b else None,
                             "contrast": c[0] if c else None, "contrast_max": c[1] if c else None,
                             "dimmed": idx in _saved})
            return mons
        mons = _physical(read)
        _ddc_cache.update(ts=time.time(), data=mons)
        return mons


def set_vcp(bus: int, brightness: int | None = None, contrast: int | None = None) -> list[dict]:
    if not isinstance(bus, int) or not 0 <= bus < 32:
        raise DisplayError("Monitor inválido.")
    for val in (brightness, contrast):
        if val is not None and not 0 <= int(val) <= 100:
            raise DisplayError("Valor deve estar entre 0 e 100.")

    def write(items):
        it = next((x for x in items if x[0] == bus), None)
        if not it:
            raise DisplayError("Monitor não encontrado no DDC.", 404)
        for code, val in ((VCP_BRIGHTNESS, brightness), (VCP_CONTRAST, contrast)):
            if val is not None and not ctypes.windll.dxva2.SetVCPFeature(it[1], code, int(val)):
                raise DisplayError("O monitor recusou o comando DDC/CI.", 500)
    with _ddc_lock:
        _physical(write)
    return ddc_monitors(refresh=True)


def dim(bus: int) -> list[dict]:
    """'Quase desligada': brilho e contraste em 0, lembrando os valores para restaurar."""
    cur = next((m for m in ddc_monitors(refresh=True) if m["bus"] == bus), None)
    if not cur:
        raise DisplayError("Monitor não encontrado no DDC.", 404)
    if bus not in _saved:
        _saved[bus] = {"brightness": cur.get("brightness"), "contrast": cur.get("contrast")}
        _store_saved(_saved)
    return set_vcp(bus, 0, 0)


def restore(bus: int) -> list[dict]:
    prev = _saved.pop(bus, None) or {"brightness": 80, "contrast": 70}
    _store_saved(_saved)
    return set_vcp(bus, prev.get("brightness") or 80, prev.get("contrast") or 70)
