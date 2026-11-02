"""Sessão do Windows (bloquear, estado), notificação na tela do PC e aplicativos instalados (listar/abrir).

O painel roda dentro da sessão do usuário (tarefa agendada no logon), então tem acesso à área de
trabalho: notificações, bloqueio da tela e abrir apps funcionam direto.

Desbloquear remotamente não existe no Windows: a tela de bloqueio (LogonUI) só aceita credenciais
digitadas ou do Windows Hello. O painel só bloqueia.

Abrir aplicativos: a lista é a mesma do menu Iniciar (Get-StartApps: programas, apps da Loja, jogos
da Steam...). Para abrir, o painel pede ao Explorer (`explorer.exe shell:AppsFolder\\<id>`): o app
abre como o usuário comum, mesmo o painel rodando como administrador.
"""
from __future__ import annotations

import ctypes
import hashlib
import logging
import re
import threading
import time
import winreg
from ctypes import wintypes
from pathlib import Path
from xml.sax.saxutils import escape

from .config import ROOT
from .win import HOME, powershell_json, run

log = logging.getLogger(__name__)
USER = HOME.name
AUMID = "SelfMonitoring.Panel"
ICON_DIR = ROOT / "data" / "icons"


class SessionError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# ---------- notificação na tela ----------
def _ensure_aumid() -> None:
    """Registra o nome/ícone das notificações (apps sem instalador precisam disso no Windows 10/11)."""
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{AUMID}") as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, "Self Monitoring")
            ico = ROOT / "web" / "icon.png"
            if ico.exists():
                winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, str(ico))
    except OSError:
        pass


_aumid_done = False


def notify_pc(title: str, body: str, urgency: str = "normal", icon: str = "", timeout_ms: int = 10000) -> bool:
    """Notificação (toast) na tela do PC. Retorna False se não foi possível exibir
    (ex.: notificações do app desativadas nas Configurações)."""
    global _aumid_done
    try:
        from winrt.windows.data.xml.dom import XmlDocument
        from winrt.windows.ui.notifications import NotificationSetting, ToastNotification, ToastNotificationManager
    except ImportError:
        return False
    if not _aumid_done:
        _ensure_aumid()
        _aumid_done = True
    scenario = ' scenario="urgent"' if urgency == "critical" else ""
    xml = (f'<toast duration="{"long" if timeout_ms > 7000 else "short"}"{scenario}><visual><binding template="ToastGeneric">'
           f"<text>{escape(title)}</text><text>{escape(body)}</text></binding></visual></toast>")
    try:
        notifier = ToastNotificationManager.create_toast_notifier_with_id(AUMID)
        if notifier.setting != NotificationSetting.ENABLED:
            return False
        doc = XmlDocument()
        doc.load_xml(xml)
        notifier.show(ToastNotification(doc))
        return True
    except OSError as e:
        log.warning("notificação falhou: %s", e)
        return False


# ---------- sessão ----------
class _WTSINFOEX_LEVEL1(ctypes.Structure):
    _fields_ = [("SessionId", wintypes.ULONG), ("SessionState", ctypes.c_int), ("SessionFlags", wintypes.LONG),
                ("WinStationName", wintypes.WCHAR * 33), ("UserName", wintypes.WCHAR * 21),
                ("DomainName", wintypes.WCHAR * 18), ("LogonTime", ctypes.c_longlong),
                ("ConnectTime", ctypes.c_longlong), ("DisconnectTime", ctypes.c_longlong),
                ("LastInputTime", ctypes.c_longlong), ("CurrentTime", ctypes.c_longlong)]


class _WTSINFOEX(ctypes.Structure):
    _fields_ = [("Level", wintypes.DWORD), ("Data", _WTSINFOEX_LEVEL1)]


WTS_SESSIONSTATE_LOCK = 0
WTSSessionInfoEx = 25


def _filetime(v: int) -> float | None:
    return v / 1e7 - 11644473600 if v > 0 else None


def graphical_session() -> dict | None:
    """Sessão do Windows em que o painel roda (a do usuário que fez logon)."""
    sid = wintypes.DWORD()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(ctypes.windll.kernel32.GetCurrentProcessId(), ctypes.byref(sid)):
        return None
    if sid.value == 0:      # sessão 0 = serviços: sem área de trabalho
        return None
    buf, size = ctypes.c_void_p(), wintypes.DWORD()
    wts = ctypes.windll.wtsapi32
    if not wts.WTSQuerySessionInformationW(None, sid.value, WTSSessionInfoEx, ctypes.byref(buf), ctypes.byref(size)):
        return None
    try:
        info = ctypes.cast(buf, ctypes.POINTER(_WTSINFOEX)).contents.Data
        station = info.WinStationName
        return {"id": str(sid.value), "type": "rdp" if station.upper().startswith("RDP") else "console",
                "locked": info.SessionFlags == WTS_SESSIONSTATE_LOCK, "active": info.SessionState == 0,
                "desktop": "Windows", "user": info.UserName, "since": _filetime(info.LogonTime)}
    finally:
        wts.WTSFreeMemory(buf)


def autologin_status() -> dict:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon") as k:
            def val(n):
                try:
                    return str(winreg.QueryValueEx(k, n)[0])
                except OSError:
                    return ""
            on = val("AutoAdminLogon") == "1"
            user = val("DefaultUserName") if on else ""
    except OSError:
        on, user = False, ""
    return {"autologin_user": user or None, "autologin_me": on and user.lower() == USER.lower(), "lock_on_start": False}


def session_status() -> dict:
    return {"user": USER, "session": graphical_session(), "can_unlock": False, **autologin_status()}


def set_locked(lock: bool) -> dict:
    if not lock:
        raise SessionError("O Windows não permite desbloquear a tela remotamente: a tela de bloqueio só aceita "
                           "a senha/PIN digitados no PC (ou Windows Hello).", 501)
    if not graphical_session():
        raise SessionError("O painel não está rodando na sessão do usuário.", 409)
    if not ctypes.windll.user32.LockWorkStation():
        raise SessionError("O Windows recusou o bloqueio.", 500)
    for _ in range(10):
        time.sleep(0.3)
        cur = graphical_session()
        if cur and cur["locked"]:
            return {"ok": True, "session": cur}
    return {"ok": True, "session": graphical_session(), "note": "pedido enviado; o estado ainda não mudou"}


# ---------- aplicativos ----------
KNOWN_SYSTEM = ("{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}", "{D65231B0-B2F1-4857-A4CE-A8E7C6EA7D27}",
                "{F38BF404-1D43-42F2-9305-67DE0B28FC23}", "Microsoft.AutoGenerated.", "Microsoft.Windows.",
                "MicrosoftWindows.", "windows.immersivecontrolpanel")
HIDE = re.compile(r"\b(uninstall|desinstalar|remover|readme|leia-me|license|licen[çc]a|help|ajuda|website|manual)\b", re.I)
GAME_LAUNCHERS = ("steam://", "com.epicgames.launcher://", "uplay://", "origin2://", "battlenet://")


def category(aid: str, name: str) -> str:
    if aid.startswith(GAME_LAUNCHERS):
        return "Jogos"
    if aid.startswith(KNOWN_SYSTEM) or aid.startswith("Microsoft.") and "!" in aid:
        return "Windows"
    if "!" in aid:
        return "Loja"
    return "Programas"


class Apps:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: tuple[float, list[dict], dict[str, dict]] | None = None

    def list(self) -> list[dict]:
        with self._lock:
            if self._cache and time.time() - self._cache[0] < 300:
                return self._cache[1]
            raw = powershell_json("Get-StartApps | Select-Object Name, AppID", timeout=60)
            apps: dict[str, dict] = {}
            for a in raw:
                aid, name = str(a.get("AppID") or ""), str(a.get("Name") or "").strip()
                if not aid or not name or HIDE.search(name) or aid in apps:
                    continue
                apps[aid] = {"id": _app_key(aid), "appid": aid, "name": name, "generic": None, "comment": aid,
                             "category": category(aid, name), "terminal": False, "flatpak": False,
                             "keywords": name.lower(), "icon": True}
            visible = sorted(apps.values(), key=lambda a: a["name"].lower())
            self._cache = (time.time(), visible, {a["id"]: a for a in visible})
            return visible

    def get(self, key: str) -> dict | None:
        self.list()
        return self._cache[2].get(key) if self._cache else None

    def icon_png(self, key: str) -> bytes | None:
        app = self.get(key)
        if not app:
            return None
        ICON_DIR.mkdir(parents=True, exist_ok=True)
        f = ICON_DIR / f"{key}.png"
        if f.is_file():
            return f.read_bytes()
        m = re.match(r"steam://rungameid/(\d+)$", app["appid"])
        if m:
            p = steam_cache_icon(m.group(1))
            if p:
                return p.read_bytes()
        png = shell_icon_png(f"shell:AppsFolder\\{app['appid']}")
        if png:
            f.write_bytes(png)
        return png

    def launch(self, key: str) -> dict:
        app = self.get(key)
        if not app:
            raise SessionError("Aplicativo não encontrado.", 404)
        s = graphical_session()
        if not s:
            raise SessionError("O painel não está rodando na sessão do usuário.", 409)
        target = app["appid"] if app["appid"].startswith(GAME_LAUNCHERS) else f"shell:AppsFolder\\{app['appid']}"
        # via Explorer: o app abre como usuário comum (sem herdar o administrador do painel)
        try:
            run(["explorer.exe", target], timeout=10)
        except OSError as e:
            raise SessionError(f"Falha ao abrir: {e}", 500) from None
        log.info("app aberto pelo painel: %s", app["name"])
        return {"ok": True, "name": app["name"], "locked": s["locked"]}


def _app_key(aid: str) -> str:
    return hashlib.sha1(aid.encode()).hexdigest()[:16]


APPS = Apps()


# ---------- ícones (IShellItemImageFactory) ----------
class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class _BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG), ("bmHeight", wintypes.LONG),
                ("bmWidthBytes", wintypes.LONG), ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                ("bmBits", ctypes.c_void_p)]


def hbitmap_bgra(hbm) -> tuple[bytes, int, int] | None:
    """HBITMAP -> (pixels BGRA de cima para baixo, largura, altura)."""
    gdi = ctypes.windll.gdi32
    bm = _BITMAP()
    if not gdi.GetObjectW(hbm, ctypes.sizeof(bm), ctypes.byref(bm)):
        return None
    w, h = bm.bmWidth, abs(bm.bmHeight)
    bi = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    hdc = ctypes.windll.user32.GetDC(None)
    try:
        if not gdi.GetDIBits(hdc, hbm, 0, h, buf, ctypes.byref(bi), 0):
            return None
    finally:
        ctypes.windll.user32.ReleaseDC(None, hdc)
    return buf.raw, w, h


def _unpremultiply(bgra: bytes) -> bytes:
    """Ícones do shell vêm com alfa pré-multiplicado; o PNG espera alfa "puro"."""
    px = bytearray(bgra)
    for i in range(0, len(px), 4):
        a = px[i + 3]
        if 0 < a < 255:
            px[i] = min(255, px[i] * 255 // a)
            px[i + 1] = min(255, px[i + 1] * 255 // a)
            px[i + 2] = min(255, px[i + 2] * 255 // a)
    return bytes(px)


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


try:
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    class IShellItemImageFactory(IUnknown):
        _iid_ = GUID("{bcc18b79-ba16-442f-80c4-8a59c30c463b}")
        _methods_ = [COMMETHOD([], HRESULT, "GetImage", (["in"], _SIZE, "size"), (["in"], ctypes.c_int, "flags"),
                               (["out"], ctypes.POINTER(wintypes.HBITMAP), "phbm"))]

    _SHCreate = ctypes.windll.shell32.SHCreateItemFromParsingName
    _SHCreate.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(GUID),
                          ctypes.POINTER(ctypes.POINTER(IShellItemImageFactory))]
except ImportError:
    comtypes = None


def shell_icon_png(parsing_name: str, size: int = 64) -> bytes | None:
    """Ícone de qualquer item do shell (programa, app da Loja, atalho) em PNG."""
    if comtypes is None:
        return None
    from .win import png_from_bgra
    comtypes.CoInitialize()      # cada thread do servidor HTTP precisa iniciar o COM
    try:
        fac = ctypes.POINTER(IShellItemImageFactory)()
        if _SHCreate(parsing_name, None, ctypes.byref(IShellItemImageFactory._iid_), ctypes.byref(fac)):
            return None
        try:
            hbm = fac.GetImage(_SIZE(size, size), 0x4 | 0x1)   # SIIGBF_ICONONLY | SIIGBF_BIGGERSIZEOK
        except OSError:
            return None
        try:
            r = hbitmap_bgra(hbm)
        finally:
            ctypes.windll.gdi32.DeleteObject(hbm)
        if not r:
            return None
        bgra, w, h = r
        return png_from_bgra(_unpremultiply(bgra), w, h, alpha=True)
    finally:
        comtypes.CoUninitialize()


# ---------- Steam ----------
def _steam_roots() -> list[Path]:
    roots = []
    for hive, path in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                       (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
        try:
            with winreg.OpenKey(hive, path) as k:
                for name in ("SteamPath", "InstallPath"):
                    try:
                        roots.append(Path(winreg.QueryValueEx(k, name)[0]))
                    except OSError:
                        pass
        except OSError:
            pass
    roots.append(Path(r"C:\Program Files (x86)\Steam"))
    out = []
    for r in roots:
        try:
            r = r.resolve()
        except OSError:
            continue
        if r not in out and r.is_dir():
            out.append(r)
    return out


STEAM_ROOTS = _steam_roots()


def steam_cache_icon(appid: str) -> Path | None:
    """O cache da biblioteca da Steam tem o ícone do jogo (quadrado, nome em hash) ou a capa."""
    if not appid.isdigit():
        return None
    for root in STEAM_ROOTS:
        d = root / "appcache" / "librarycache" / appid
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.suffix == ".jpg" and len(f.stem) == 40:
                return f
        for name in ("library_600x900.jpg", "header.jpg"):
            if (d / name).is_file():
                return d / name
        old = root / "appcache" / "librarycache" / f"{appid}_icon.jpg"
        if old.is_file():
            return old
    return None
