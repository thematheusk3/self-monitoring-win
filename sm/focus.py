"""Janela em foco no PC (Win32: GetForegroundWindow).

Apps da Loja (UWP) rodam dentro de uma moldura do ApplicationFrameHost.exe; nesse caso o
processo real é o dono da janela-filha "Windows.UI.Core.CoreWindow".
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

import psutil

from .desktop import graphical_session
from .win import version_info

user32 = ctypes.windll.user32
IGNORE_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}   # área de trabalho, barra de tarefas


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


def _text(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _cls(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _pid(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _uwp_pid(hwnd, frame_pid: int) -> int | None:
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(child, _):
        p = _pid(child)
        if p != frame_pid and _cls(child) == "Windows.UI.Core.CoreWindow":
            found.append(p)
            return False
        return True
    user32.EnumChildWindows(hwnd, cb, 0)
    return found[0] if found else None


def _fullscreen(hwnd) -> bool:
    r = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return False
    mon = user32.MonitorFromWindow(hwnd, 2)
    mi = _MONITORINFO(ctypes.sizeof(_MONITORINFO))
    if not user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return False
    m = mi.rcMonitor
    return r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom


def foreground() -> dict | None:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    cls = _cls(hwnd)
    if cls in IGNORE_CLASSES:
        return None
    pid = _pid(hwnd)
    exe = name = None
    try:
        p = psutil.Process(pid)
        name = p.name()
        if name.lower() == "applicationframehost.exe":
            real = _uwp_pid(hwnd, pid)
            if real:
                pid, p = real, psutil.Process(real)
                name = p.name()
        exe = p.exe()
    except (psutil.Error, OSError):
        pass
    desc = version_info(exe).get("description") if exe else None
    return {"caption": _text(hwnd), "cls": (name or "").removesuffix(".exe"), "name": name or "",
            "pid": pid or None, "desktop": desc or "", "fullscreen": _fullscreen(hwnd), "hwnd": int(hwnd)}


class FocusMonitor:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.window: dict | None = None
        self.since: float | None = None
        self._session: dict | None = None
        self._last_check = 0.0

    def status(self) -> dict:
        now = time.time()
        if now - self._last_check > 5:
            self._last_check = now
            self._session = graphical_session()
        s = self._session
        w = None if (s and s.get("locked")) else foreground()
        with self.lock:
            same = (self.window or {}).get("pid") == (w or {}).get("pid") and \
                   (self.window or {}).get("hwnd") == (w or {}).get("hwnd")
            if not same or self.since is None:
                self.since = now
            self.window = w
            return {"window": dict(w) if w else None, "since": self.since if w else None, "source": "win32",
                    "locked": bool(s and s.get("locked")), "session": bool(s)}
