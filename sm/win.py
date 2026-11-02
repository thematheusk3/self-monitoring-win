"""Utilidades do Windows compartilhadas: subprocessos sem janela, PowerShell, admin, PNG.

O painel roda com pythonw (sem console). Todo subprocesso precisa de CREATE_NO_WINDOW,
senão cada chamada (nvidia-smi, powershell...) pisca uma janela preta na tela do PC.
"""
from __future__ import annotations

import ctypes
import json
import os
import struct
import subprocess
import zlib
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000
HOME = Path.home()
SYSTEMROOT = Path(os.environ.get("SystemRoot", r"C:\Windows"))
POWERSHELL = str(SYSTEMROOT / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def run(cmd: list[str], timeout: float = 10, **kw) -> subprocess.CompletedProcess:
    """subprocess.run com saída de texto, sem janela de console."""
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("encoding", "utf-8")
    kw.setdefault("errors", "replace")
    return subprocess.run(cmd, timeout=timeout, creationflags=CREATE_NO_WINDOW, **kw)


def powershell(script: str, timeout: float = 30) -> subprocess.CompletedProcess:
    """Roda um script no Windows PowerShell 5.1 com saída UTF-8."""
    pre = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;$ProgressPreference='SilentlyContinue';"
    return run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", pre + script],
               timeout=timeout)


def powershell_json(script: str, timeout: float = 30):
    """Roda `script | ConvertTo-Json` e devolve sempre uma lista (ou [] em caso de erro)."""
    r = powershell(f"@({script}) | ConvertTo-Json -Depth 4 -Compress", timeout=timeout)
    try:
        data = json.loads(r.stdout or "null")
    except ValueError:
        return []
    if data is None:
        return []
    return data if isinstance(data, list) else [data]


def ps_quote(s: str) -> str:
    """String literal segura para PowerShell (aspas simples)."""
    return "'" + str(s).replace("'", "''") + "'"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def enable_dpi_awareness() -> None:
    """Coordenadas e capturas em pixels reais (sem isso, telas com escala 150% saem reduzidas)."""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass


# ---------- informações de versão de um .exe ----------
_ver_cache: dict[str, dict] = {}


def version_info(path: str) -> dict:
    """FileDescription, CompanyName, ProductName e FileVersion do executável (cache por caminho)."""
    if path in _ver_cache:
        return _ver_cache[path]
    out: dict = {}
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(path, None)
        if size:
            buf = ctypes.create_string_buffer(size)
            if ver.GetFileVersionInfoW(path, 0, size, buf):
                ptr, n = ctypes.c_void_p(), ctypes.c_uint()
                langs = [(0x0409, 0x04B0), (0x0416, 0x04B0), (0x0409, 0x04E4), (0x0000, 0x04B0)]
                if ver.VerQueryValueW(buf, r"\VarFileInfo\Translation", ctypes.byref(ptr), ctypes.byref(n)) and n.value >= 4:
                    arr = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_ushort))
                    langs.insert(0, (arr[0], arr[1]))
                for key, name in (("FileDescription", "description"), ("CompanyName", "company"),
                                  ("ProductName", "product"), ("FileVersion", "version")):
                    for lang, cp in langs:
                        q = rf"\StringFileInfo\{lang:04X}{cp:04X}\{key}"
                        if ver.VerQueryValueW(buf, q, ctypes.byref(ptr), ctypes.byref(n)) and n.value > 1:
                            v = ctypes.wstring_at(ptr, n.value - 1).strip()
                            if v:
                                out[name] = v
                                break
    except (OSError, AttributeError, ValueError):
        pass
    if len(_ver_cache) > 4000:
        _ver_cache.clear()
    _ver_cache[path] = out
    return out


# ---------- PNG (sem Pillow) ----------
def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png_from_bgra(bgra: bytes, width: int, height: int, alpha: bool = False, level: int = 6) -> bytes:
    """Converte pixels BGRA (de cima para baixo) em PNG. A troca de canais usa fatias, que rodam em C."""
    n = width * height
    ch = 4 if alpha else 3
    out = bytearray(n * ch)
    out[0::ch] = bgra[2::4]
    out[1::ch] = bgra[1::4]
    out[2::ch] = bgra[0::4]
    if alpha:
        out[3::4] = bgra[3::4]
    stride = width * ch
    raw = b"".join(b"\x00" + bytes(out[y * stride:(y + 1) * stride]) for y in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6 if alpha else 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw, level)) + _chunk(b"IEND", b"")
