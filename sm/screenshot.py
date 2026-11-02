"""Captura de tela pelo painel.

Regra: TODA captura mostra antes um aviso visível na tela do PC (notificação do Windows).
Se o aviso não puder ser exibido (ex.: notificações do app desativadas), a captura é recusada.
Captura todos os monitores (área de trabalho virtual) via GDI; jogos em tela cheia exclusiva
podem sair pretos.
"""
from __future__ import annotations

import ctypes
import logging
import re
import time
from ctypes import wintypes
from pathlib import Path

from .config import ROOT
from .desktop import _BITMAPINFOHEADER, graphical_session, notify_pc
from .win import png_from_bgra

log = logging.getLogger(__name__)
DIR = ROOT / "data" / "screenshots"
KEEP = 30            # mantém as últimas N capturas
_NAME = re.compile(r"^shot-\d{8}-\d{6}(?:-\d{1,3})?\.png$")
PNG_END = b"IEND\xaeB`\x82"     # último chunk de um PNG completo
SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000


class ShotError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def grab() -> tuple[bytes, int, int]:
    """Pixels BGRA de todos os monitores."""
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    x, y = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
    w, h = u.GetSystemMetrics(78), u.GetSystemMetrics(79)
    screen = u.GetDC(None)
    mem = g.CreateCompatibleDC(screen)
    bits = ctypes.c_void_p()
    bi = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    g.CreateDIBSection.restype = wintypes.HBITMAP
    g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    dib = g.CreateDIBSection(screen, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0)
    try:
        if not dib:
            raise ShotError("Falha ao criar a imagem da captura.", 500)
        old = g.SelectObject(mem, dib)
        ok = g.BitBlt(mem, 0, 0, w, h, screen, x, y, SRCCOPY | CAPTUREBLT)
        g.SelectObject(mem, old)
        if not ok:
            raise ShotError("O Windows não permitiu capturar a tela.", 500)
        return ctypes.string_at(bits, w * h * 4), w, h
    finally:
        if dib:
            g.DeleteObject(dib)
        g.DeleteDC(mem)
        u.ReleaseDC(None, screen)


def take(requested_by: str) -> dict:
    s = graphical_session()
    if not s:
        raise ShotError("O painel não está rodando na sessão do usuário: não há tela para capturar.", 409)
    if s["locked"]:
        raise ShotError("A tela do PC está bloqueada; o Windows não deixa capturar a tela de bloqueio.", 409)
    # 1) aviso visível no PC (obrigatório)
    if not notify_pc("📸 Captura de tela pelo painel",
                     f"O self-monitoring tirou uma captura desta tela agora (pedido de {requested_by}).",
                     urgency="critical", timeout_ms=8000):
        raise ShotError("Não foi possível mostrar o aviso na tela do PC (as notificações do Self Monitoring estão "
                        "desativadas?); por segurança a captura não foi feita.", 503)
    # 2) captura
    DIR.mkdir(parents=True, exist_ok=True)
    stem = time.strftime("shot-%Y%m%d-%H%M%S")
    name = f"{stem}.png"
    n = 1
    while (DIR / name).exists():      # duas capturas no mesmo segundo
        name = f"{stem}-{n}.png"
        n += 1
    path = DIR / name
    bgra, w, h = grab()
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(png_from_bgra(bgra, w, h, level=3))
    tmp.replace(path)                 # aparece de uma vez: nunca é servida pela metade
    log.warning("captura de tela feita pelo painel (pedido de %s): %s", requested_by, name)
    _prune()
    return {"ok": True, "name": name, "size": path.stat().st_size}


def png_complete(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            if f.read(8) != b"\x89PNG\r\n\x1a\n":
                return False
            f.seek(-8, 2)
            return f.read(8) == PNG_END
    except OSError:
        return False


def _prune() -> None:
    files = sorted(DIR.glob("shot-*.png"))
    for f in files[:-KEEP]:
        f.unlink(missing_ok=True)


def list_shots() -> list[dict]:
    if not DIR.is_dir():
        return []
    files = [f for f in DIR.glob("shot-*.png") if _NAME.match(f.name)]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"name": f.name, "size": f.stat().st_size, "time": f.stat().st_mtime} for f in files]


def path_of(name: str) -> Path | None:
    if not _NAME.match(name or ""):
        return None
    p = DIR / name
    return p if p.is_file() else None


def delete(name: str) -> bool:
    p = path_of(name)
    if p:
        p.unlink()
        return True
    return False
