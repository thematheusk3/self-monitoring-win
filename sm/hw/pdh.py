"""Contadores de desempenho do Windows (PDH) via ctypes, os mesmos que o Gerenciador de Tarefas usa.

    q = Query()
    eng = q.add(r"\\GPU Engine(*)\\Utilization Percentage")
    q.collect(); time.sleep(1); q.collect()
    eng.values()   # {"pid_123_luid_..._engtype_3D": 12.5, ...}

Contadores de taxa (% de uso) precisam de duas coletas; a primeira só serve de base.
Caminhos com (*) trazem todas as instâncias, inclusive as que surgem depois.
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

PDH_FMT_DOUBLE = 0x00000200
PDH_FMT_NOCAP100 = 0x00008000
PDH_MORE_DATA = 0x800007D2
PDH_CSTATUS_VALID_DATA = 0x0
PDH_CSTATUS_NEW_DATA = 0x1

try:
    _pdh = ctypes.WinDLL("pdh.dll")
except OSError:          # fora do Windows (CI/testes)
    _pdh = None


class _Value(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]


class _Item(ctypes.Structure):
    _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _Value)]


class Counter:
    def __init__(self, query: "Query", handle: wintypes.HANDLE) -> None:
        self.query = query
        self.handle = handle

    def values(self) -> dict[str, float]:
        """Instância -> valor (contadores com (*))."""
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        fmt = PDH_FMT_DOUBLE | PDH_FMT_NOCAP100
        rc = _pdh.PdhGetFormattedCounterArrayW(self.handle, fmt, ctypes.byref(size), ctypes.byref(count), None)
        if rc & 0xFFFFFFFF != PDH_MORE_DATA or not size.value:
            return {}
        buf = ctypes.create_string_buffer(size.value)
        rc = _pdh.PdhGetFormattedCounterArrayW(self.handle, fmt, ctypes.byref(size), ctypes.byref(count), buf)
        if rc:
            return {}
        items = ctypes.cast(buf, ctypes.POINTER(_Item))
        out = {}
        for i in range(count.value):
            it = items[i]
            if it.FmtValue.CStatus in (PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA) and it.szName:
                out[it.szName] = out.get(it.szName, 0.0) + it.FmtValue.doubleValue
        return out

    def value(self) -> float | None:
        """Valor de um contador sem (*)."""
        v = _Value()
        rc = _pdh.PdhGetFormattedCounterValue(self.handle, PDH_FMT_DOUBLE | PDH_FMT_NOCAP100, None, ctypes.byref(v))
        if rc or v.CStatus not in (PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA):
            return None
        return v.doubleValue


class Query:
    def __init__(self) -> None:
        if _pdh is None:
            raise OSError("PDH indisponível (não é Windows)")
        self.handle = wintypes.HANDLE()
        if _pdh.PdhOpenQueryW(None, None, ctypes.byref(self.handle)):
            raise OSError("PdhOpenQuery falhou")
        self.lock = threading.Lock()

    def add(self, path: str) -> Counter | None:
        """Adiciona um contador pelo nome em inglês (funciona em Windows em português). None se não existir."""
        h = wintypes.HANDLE()
        if _pdh.PdhAddEnglishCounterW(self.handle, path, None, ctypes.byref(h)):
            return None
        return Counter(self, h)

    def collect(self) -> bool:
        with self.lock:
            return _pdh.PdhCollectQueryData(self.handle) == 0

    def close(self) -> None:
        if self.handle:
            _pdh.PdhCloseQuery(self.handle)
            self.handle = None
