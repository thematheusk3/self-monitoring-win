"""GPUs no Windows: lista (DXGI), uso por adaptador e por processo (contadores "GPU Engine",
os mesmos do Gerenciador de Tarefas) e detalhes da NVIDIA (NVML, a biblioteca do driver).

- DXGI dá nome, fabricante, VRAM dedicada/compartilhada e o LUID de cada adaptador.
- Os contadores PDH "GPU Engine(pid_X_luid_Y_..._engtype_Z)" dão o uso de cada engine (3D, cópia,
  vídeo...) por processo. Como no Gerenciador de Tarefas: uso do processo = maior soma entre os tipos
  de engine; uso da GPU = idem somando todos os processos.
- NVIDIA: NVML (nvml.dll, instalada com o driver) dá temperatura, potência, clocks e ventoinha.
  Sem NVML, cai para o nvidia-smi.
- AMD/Intel: temperatura e potência só com o LibreHardwareMonitor (ver hw/lhm.py).
"""
from __future__ import annotations

import ctypes
import logging
import re
import shutil
from ctypes import wintypes

from .pdh import Query

log = logging.getLogger(__name__)
VENDORS = {0x10DE: "NVIDIA", 0x1002: "AMD", 0x1022: "AMD", 0x8086: "Intel", 0x1414: "Microsoft"}
DXGI_ADAPTER_FLAG_SOFTWARE = 2
_ENG = re.compile(r"pid_(\d+)_luid_(0x[0-9A-Fa-f]+_0x[0-9A-Fa-f]+)_phys_\d+_eng_\d+_engtype_(.*)$")
_PMEM = re.compile(r"pid_(\d+)_luid_(0x[0-9A-Fa-f]+_0x[0-9A-Fa-f]+)_phys_\d+$")
_AMEM = re.compile(r"luid_(0x[0-9A-Fa-f]+_0x[0-9A-Fa-f]+)_phys_\d+$")


# ---------- DXGI ----------
class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _DESC1(ctypes.Structure):
    _fields_ = [("Description", wintypes.WCHAR * 128), ("VendorId", wintypes.UINT), ("DeviceId", wintypes.UINT),
                ("SubSysId", wintypes.UINT), ("Revision", wintypes.UINT), ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                ("AdapterLuid", _LUID), ("Flags", wintypes.UINT)]


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


def _guid(s: str) -> _GUID:
    import uuid
    u = uuid.UUID(s)
    g = _GUID()
    g.Data1, g.Data2, g.Data3 = u.time_low, u.time_mid, u.time_hi_version
    g.Data4[:] = list(u.bytes[8:])
    return g


def _vcall(obj: ctypes.c_void_p, index: int, restype, *args):
    """Chama o método `index` da vtable de um objeto COM."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    types = [type(a) if isinstance(a, ctypes._SimpleCData) else ctypes.c_void_p for a in args]   # byref() -> ponteiro
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *types)(vtbl[index])
    return fn(obj, *args)


def dxgi_adapters() -> list[dict]:
    """[{name, vendor_id, device_id, luid, dedicated, shared}] das placas reais (sem o renderizador por software)."""
    try:
        dxgi = ctypes.WinDLL("dxgi.dll")
    except OSError:
        return []
    factory = ctypes.c_void_p()
    if dxgi.CreateDXGIFactory1(ctypes.byref(_guid("770aae78-f26f-4dba-a829-253c83d1b387")), ctypes.byref(factory)):
        return []
    out, seen = [], set()
    try:
        i = 0
        while True:
            adapter = ctypes.c_void_p()
            if _vcall(factory, 12, ctypes.c_long, wintypes.UINT(i), ctypes.byref(adapter)):   # EnumAdapters1
                break                                   # DXGI_ERROR_NOT_FOUND: acabou a lista
            i += 1
            d = _DESC1()
            _vcall(adapter, 10, ctypes.c_long, ctypes.byref(d))   # IDXGIAdapter1::GetDesc1
            _vcall(adapter, 2, ctypes.c_ulong)                     # Release
            luid = f"0x{d.AdapterLuid.HighPart & 0xFFFFFFFF:08X}_0x{d.AdapterLuid.LowPart:08X}"
            if d.Flags & DXGI_ADAPTER_FLAG_SOFTWARE or d.VendorId == 0x1414 or luid in seen:
                continue
            seen.add(luid)
            out.append({"name": d.Description.strip(), "vendor_id": d.VendorId, "device_id": d.DeviceId,
                        "luid": luid, "dedicated": d.DedicatedVideoMemory, "shared": d.SharedSystemMemory})
    finally:
        _vcall(factory, 2, ctypes.c_ulong)
    return out


# ---------- uso por engine (PDH) ----------
class GpuEngines:
    """Atualizado pelo coletor de GPU a cada amostra; o de processos lê per_pid."""

    def __init__(self) -> None:
        self.per_pid: dict[int, dict] = {}            # pid -> {"gpu": %, "gpu_mem": bytes}
        self.per_luid: dict[str, float] = {}           # luid -> % (maior tipo de engine)
        self.per_luid_engines: dict[str, dict] = {}    # luid -> {tipo: %}
        self.mem: dict[str, dict] = {}                 # luid -> {"dedicated": bytes, "shared": bytes}
        try:
            self.q = Query()
            self.eng = self.q.add(r"\GPU Engine(*)\Utilization Percentage")
            self.pmem = self.q.add(r"\GPU Process Memory(*)\Dedicated Usage")
            self.adm = self.q.add(r"\GPU Adapter Memory(*)\Dedicated Usage")
            self.ash = self.q.add(r"\GPU Adapter Memory(*)\Shared Usage")
            self.q.collect()
        except OSError:
            self.q = None

    def update(self) -> None:
        if not self.q or not self.eng or not self.q.collect():
            return
        by_pid: dict[int, dict[str, float]] = {}
        by_luid: dict[str, dict[str, float]] = {}
        for inst, v in self.eng.values().items():
            m = _ENG.match(inst)
            if not m or v <= 0:
                continue
            pid, luid, et = int(m.group(1)), m.group(2), m.group(3)
            t = by_pid.setdefault(pid, {})
            t[et] = t.get(et, 0.0) + v
            t = by_luid.setdefault(luid, {})
            t[et] = t.get(et, 0.0) + v
        pmem: dict[int, float] = {}
        for inst, v in (self.pmem.values() if self.pmem else {}).items():
            m = _PMEM.match(inst)
            if m:
                pmem[int(m.group(1))] = pmem.get(int(m.group(1)), 0) + v
        self.per_pid = {pid: {"gpu": round(min(100.0, max(e.values())), 1), "gpu_mem": int(pmem.get(pid, 0))}
                        for pid, e in by_pid.items()}
        for pid, b in pmem.items():
            if pid not in self.per_pid and b > 0:
                self.per_pid[pid] = {"gpu": 0.0, "gpu_mem": int(b)}
        self.per_luid = {luid: min(100.0, max(e.values())) for luid, e in by_luid.items()}
        self.per_luid_engines = {luid: {k: round(min(100.0, v), 1) for k, v in e.items()} for luid, e in by_luid.items()}
        mem: dict[str, dict] = {}
        for key, c in (("dedicated", self.adm), ("shared", self.ash)):
            for inst, v in (c.values() if c else {}).items():
                m = _AMEM.match(inst)
                if m:
                    mem.setdefault(m.group(1), {})[key] = int(v)
        self.mem = mem


# ---------- NVML ----------
class _NvUtil(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _NvMem(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class _NvPci(ctypes.Structure):
    _fields_ = [("busIdLegacy", ctypes.c_char * 16), ("domain", ctypes.c_uint), ("bus", ctypes.c_uint),
                ("device", ctypes.c_uint), ("pciDeviceId", ctypes.c_uint), ("pciSubSystemId", ctypes.c_uint),
                ("busId", ctypes.c_char * 32)]


class Nvml:
    def __init__(self) -> None:
        self.lib = None
        for path in ("nvml.dll", r"C:\Program Files\NVIDIA Corporation\NVSMI\nvml.dll"):
            try:
                self.lib = ctypes.CDLL(path)
                break
            except OSError:
                continue
        if self.lib is None or self.lib.nvmlInit_v2():
            self.lib = None

    def devices(self) -> list[tuple[ctypes.c_void_p, int, str]]:
        """[(handle, pciDeviceId, busId)]"""
        if not self.lib:
            return []
        n = ctypes.c_uint()
        if self.lib.nvmlDeviceGetCount_v2(ctypes.byref(n)):
            return []
        out = []
        for i in range(n.value):
            h = ctypes.c_void_p()
            if self.lib.nvmlDeviceGetHandleByIndex_v2(i, ctypes.byref(h)):
                continue
            pci = _NvPci()
            self.lib.nvmlDeviceGetPciInfo_v3(h, ctypes.byref(pci))
            out.append((h, pci.pciDeviceId, pci.busId.decode(errors="replace").lower()))
        return out

    def _uint(self, fn: str, h, *args) -> int | None:
        v = ctypes.c_uint()
        return v.value if getattr(self.lib, fn)(h, *args, ctypes.byref(v)) == 0 else None

    def sample(self, h) -> dict:
        lib = self.lib
        u, m = _NvUtil(), _NvMem()
        busy = u.gpu if lib.nvmlDeviceGetUtilizationRates(h, ctypes.byref(u)) == 0 else None
        mem = {"used": m.used, "total": m.total} if lib.nvmlDeviceGetMemoryInfo(h, ctypes.byref(m)) == 0 else None
        temp = self._uint("nvmlDeviceGetTemperature", h, ctypes.c_int(0))
        mw = self._uint("nvmlDeviceGetPowerUsage", h)
        sclk = self._uint("nvmlDeviceGetClockInfo", h, ctypes.c_int(0))
        mclk = self._uint("nvmlDeviceGetClockInfo", h, ctypes.c_int(2))
        fan = self._uint("nvmlDeviceGetFanSpeed", h)
        return {"busy": busy, "temp": temp, "power": round(mw / 1000, 1) if mw is not None else None,
                "clocks": {k: v for k, v in (("sclk", sclk), ("mclk", mclk)) if v is not None},
                "fan_pct": fan, "vram": mem}


def _nvidia_smi(index: int) -> dict:
    """Plano B sem NVML."""
    from ..win import run
    fields = "utilization.gpu,temperature.gpu,power.draw,clocks.gr,clocks.mem,memory.used,memory.total,fan.speed"
    try:
        out = run(["nvidia-smi", f"--id={index}", f"--query-gpu={fields}", "--format=csv,noheader,nounits"], timeout=3).stdout
    except OSError:
        return {}
    vals = [v.strip() for v in out.strip().split(",")]

    def g(i):
        try:
            return float(vals[i])
        except (IndexError, ValueError):
            return None
    mib = 1024**2
    return {"busy": g(0), "temp": g(1), "power": g(2),
            "clocks": {k: v for k, v in (("sclk", g(3)), ("mclk", g(4))) if v is not None},
            "fan_pct": g(7), "vram": {"used": g(5) * mib, "total": g(6) * mib} if g(5) is not None and g(6) else None}


# ---------- GPU ----------
class Gpu:
    def __init__(self, a: dict, engines: GpuEngines, nvml: Nvml | None, nv_handle=None, nv_index: int | None = None) -> None:
        self.a = a
        self.engines = engines
        self.nvml, self.nv_handle, self.nv_index = nvml, nv_handle, nv_index
        self.name = a["name"]
        self.vendor = VENDORS.get(a["vendor_id"], f"0x{a['vendor_id']:04X}")
        self.pci = a["luid"]
        self.integrated = a["dedicated"] <= 2 * 1024**3 and self.vendor != "NVIDIA"
        self.driver = {"NVIDIA": "nvidia", "AMD": "amd", "Intel": "intel"}.get(self.vendor, "wddm")

    def sample(self, lhm=None) -> dict:
        luid = self.a["luid"]
        eng = self.engines.per_luid.get(luid)
        mem = self.engines.mem.get(luid, {})
        s = {"id": luid, "vendor": self.vendor, "name": self.name, "driver": self.driver, "integrated": self.integrated,
             "busy": round(eng, 1) if eng is not None else 0.0, "busy_source": "engines",
             "temp": None, "temp_source": None, "temps": {}, "power": None, "clocks": {}, "fan": None,
             "vram": {"used": mem.get("dedicated"), "total": self.a["dedicated"]} if self.a["dedicated"] else None,
             "gtt": {"used": mem.get("shared"), "total": self.a["shared"]} if self.integrated else None,
             "engines": self.engines.per_luid_engines.get(luid, {}), "notes": []}
        nv = {}
        if self.nv_handle is not None and self.nvml and self.nvml.lib:
            nv = self.nvml.sample(self.nv_handle)
        elif self.nv_index is not None:
            nv = _nvidia_smi(self.nv_index)
        if nv:
            if nv.get("busy") is not None:
                s["busy"], s["busy_source"] = float(nv["busy"]), "driver"
            s["temp"] = nv.get("temp")
            s["temp_source"] = "gpu" if s["temp"] is not None else None
            s["temps"] = {"gpu": s["temp"]} if s["temp"] is not None else {}
            s["power"], s["clocks"], s["fan_pct"] = nv.get("power"), nv.get("clocks") or {}, nv.get("fan_pct")
            if nv.get("vram"):
                s["vram"] = nv["vram"]
        if s["temp"] is None and lhm is not None:
            temps = lhm.gpu_temps(self.name)
            if temps:
                s["temps"] = temps
                s["temp"] = temps.get("GPU Core") or temps.get("GPU VR SoC") or next(iter(temps.values()))
                s["temp_source"] = "lhm"
        return s


def discover_all(engines: GpuEngines) -> list[Gpu]:
    adapters = dxgi_adapters()
    nvml = Nvml()
    nv_devs = nvml.devices()
    gpus, nv_i = [], 0
    for a in adapters:
        if a["vendor_id"] == 0x10DE:
            want = (a["device_id"] << 16) | a["vendor_id"]
            match = next((d for d in nv_devs if d[1] == want), None)
            if match:
                nv_devs.remove(match)
                gpus.append(Gpu(a, engines, nvml, nv_handle=match[0]))
            else:
                gpus.append(Gpu(a, engines, None, nv_index=nv_i if shutil.which("nvidia-smi") else None))
            nv_i += 1
        else:
            gpus.append(Gpu(a, engines, None))
    # a GPU dedicada primeiro (é a que mais interessa no painel)
    gpus.sort(key=lambda g: g.integrated)
    log.info("GPUs: %s", ", ".join(f"{g.name} ({'NVML' if g.nv_handle is not None else g.driver})" for g in gpus) or "nenhuma")
    return gpus
