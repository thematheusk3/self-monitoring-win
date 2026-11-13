"""Ícone na área de notificação (perto do relógio): mostra que o painel está rodando e dá atalhos.

Menu (botão direito; o esquerdo abre o painel):
  - versão e estado;
  - abrir o painel / abrir o Mural no navegador;
  - os endereços para entrar pela rede (clique copia), útil quando não se lembra do IP ou da porta;
  - Computador: bloquear a tela, reiniciar, desligar (com confirmação);
  - Painel: procurar atualização, atualizar agora (só quando há versão nova e a pessoa clica), abrir a configuração,
    o log e a pasta, encerrar o painel.

Win32 puro (ctypes): janela escondida + Shell_NotifyIconW. O painel roda como administrador e o Explorer não: sem
ChangeWindowMessageFilterEx o Windows descarta os cliques no ícone (UIPI). Os links abrem pelo Explorer, para o
navegador não rodar como administrador. Desliga em [tray] enabled = false.
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import threading
from ctypes import wintypes

from . import __version__
from .config import ROOT

log = logging.getLogger(__name__)
u32, s32, g32, k32 = ctypes.windll.user32, ctypes.windll.shell32, ctypes.windll.gdi32, ctypes.windll.kernel32

WM_USER, WM_COMMAND, WM_DESTROY, WM_NULL = 0x0400, 0x0111, 0x0002, 0x0000
WM_LBUTTONUP, WM_RBUTTONUP = 0x0202, 0x0205
WM_TRAY = WM_USER + 20
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4
MF_STRING, MF_GRAYED, MF_SEPARATOR, MF_POPUP = 0, 1, 0x800, 0x10
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80
MSGFLT_ALLOW = 1
MB_YESNO, MB_ICONWARNING, MB_TOPMOST, MB_SETFOREGROUND, IDYES = 0x4, 0x30, 0x40000, 0x10000, 6
CF_UNICODETEXT, GMEM_MOVEABLE = 13, 2
SM_CXSMICON = 49

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
u32.DefWindowProcW.restype = LRESULT
u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u32.CreateWindowExW.restype = wintypes.HWND
u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                wintypes.HINSTANCE, wintypes.LPVOID]
u32.CreatePopupMenu.restype = wintypes.HMENU
u32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
u32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               wintypes.HWND, wintypes.LPVOID]
u32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
k32.GlobalAlloc.restype = wintypes.HGLOBAL
k32.GlobalLock.restype = wintypes.LPVOID
k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
# handles de 64 bits: sem os tipos declarados, o ctypes corta ou recusa os valores
g32.CreateDIBSection.restype = wintypes.HBITMAP
g32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                 wintypes.HANDLE, wintypes.DWORD]
g32.CreateBitmap.restype = wintypes.HBITMAP
g32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
g32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
u32.GetDC.restype = wintypes.HDC
u32.GetDC.argtypes = [wintypes.HWND]
u32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
u32.CreateIconIndirect.restype = wintypes.HICON
u32.CreateIconIndirect.argtypes = [ctypes.c_void_p]
u32.RegisterClassExW.argtypes = [ctypes.c_void_p]
k32.GetModuleHandleW.restype = wintypes.HMODULE
k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
u32.ChangeWindowMessageFilterEx.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.DWORD, ctypes.c_void_p]
u32.SetForegroundWindow.argtypes = [wintypes.HWND]
u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u32.DestroyMenu.argtypes = [wintypes.HMENU]
u32.OpenClipboard.argtypes = [wintypes.HWND]
u32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
s32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]
k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", wintypes.HICON)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


# o mesmo desenho do web/icon.svg (64x64): fundo verde-escuro arredondado e quatro barras
_BG = (0x0d, 0x1f, 0x1a)
_BARS = [((11, 33, 8, 19), (0xe8, 0xbe, 0x57)), ((23, 17, 8, 35), (0xe8, 0xbe, 0x57)),
         ((35, 26, 8, 26), (0xe8, 0xbe, 0x57)), ((47, 12, 8, 40), (0x6f, 0xe0, 0xbe))]


def icon_pixels(size: int) -> bytes:
    """BGRA de cima para baixo, com antisserrilhado (4x4 amostras por pixel)."""
    out = bytearray(size * size * 4)
    k = 64 / size
    for py in range(size):
        for px in range(size):
            acc, cov = [0.0, 0.0, 0.0], 0.0
            for sy in range(4):
                for sx in range(4):
                    x, y = (px + (sx + .5) / 4) * k, (py + (sy + .5) / 4) * k
                    # cantos arredondados (raio 10)
                    cx, cy = min(max(x, 10), 54), min(max(y, 10), 54)
                    if (x - cx) ** 2 + (y - cy) ** 2 > 100:
                        continue
                    col = _BG
                    for (bx, by, bw, bh), c in _BARS:
                        if bx <= x < bx + bw and by <= y < by + bh:
                            col = c
                            break
                    acc = [a + c for a, c in zip(acc, col)]
                    cov += 1
            i = (py * size + px) * 4
            if cov:
                a = cov / 16
                r, g, b = (v / cov for v in acc)
                out[i:i + 4] = bytes((round(b * a), round(g * a), round(r * a), round(a * 255)))   # pré-multiplicado
    return bytes(out)


def make_icon(size: int):
    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), size, -size, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    hdc = u32.GetDC(None)
    color = g32.CreateDIBSection(hdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    u32.ReleaseDC(None, hdc)
    data = icon_pixels(size)
    ctypes.memmove(bits, data, len(data))
    mask = g32.CreateBitmap(size, size, 1, 1, None)
    info = ICONINFO(True, 0, 0, mask, color)
    hicon = u32.CreateIconIndirect(ctypes.byref(info))
    g32.DeleteObject(mask)
    g32.DeleteObject(color)
    return hicon


def copy_text(hwnd, text: str) -> bool:
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    if not u32.OpenClipboard(hwnd):
        return False
    try:
        u32.EmptyClipboard()
        h = k32.GlobalAlloc(GMEM_MOVEABLE, size)
        p = k32.GlobalLock(h)
        ctypes.memmove(p, data, size)
        k32.GlobalUnlock(h)
        return bool(u32.SetClipboardData(CF_UNICODETEXT, h))
    finally:
        u32.CloseClipboard()


def open_in_user_session(target: str) -> None:
    """Pelo Explorer: o navegador/pasta abre como o usuário, não como administrador."""
    subprocess.Popen(["explorer.exe", target], creationflags=0x08000000)


class Tray:
    def __init__(self, ctx, port: int, lan_ips) -> None:
        self.ctx, self.port, self.lan_ips = ctx, port, lan_ips
        self.hwnd = None
        self.nid = None
        self._proc = WNDPROC(self._wndproc)       # guardado: o Windows chama de volta
        self._msg_taskbar = u32.RegisterWindowMessageW("TaskbarCreated")
        self._actions: dict[int, callable] = {}

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="tray").start()

    # ---------- janela e ícone ----------
    def _run(self) -> None:
        try:
            hinst = k32.GetModuleHandleW(None)
            wc = WNDCLASSEXW(ctypes.sizeof(WNDCLASSEXW), 0, self._proc, 0, 0, hinst, None, None, None, None,
                             "SelfMonitoringTray", None)
            u32.RegisterClassExW(ctypes.byref(wc))
            self.hwnd = u32.CreateWindowExW(0, "SelfMonitoringTray", "Self Monitoring", 0, 0, 0, 0, 0, None, None, hinst, None)
            for m in (WM_TRAY, self._msg_taskbar, WM_COMMAND):
                u32.ChangeWindowMessageFilterEx(self.hwnd, m, MSGFLT_ALLOW, None)
            self.icon = make_icon(u32.GetSystemMetrics(SM_CXSMICON) or 16)
            self._add()
            msg = wintypes.MSG()
            while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                u32.TranslateMessage(ctypes.byref(msg))
                u32.DispatchMessageW(ctypes.byref(msg))
        except Exception:  # noqa: BLE001
            log.exception("ícone da área de notificação falhou")

    def _tip(self) -> str:
        ips = self.lan_ips()
        where = f"http://{ips[0]}:{self.port}" if ips else f"http://localhost:{self.port}"
        return f"Self Monitoring {__version__} rodando\n{where}"[:127]

    def _add(self) -> None:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd, nid.uID = self.hwnd, 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage, nid.hIcon, nid.szTip = WM_TRAY, self.icon, self._tip()
        self.nid = nid
        if not s32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            s32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def remove(self) -> None:
        if self.nid is not None:
            s32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                ev = lparam & 0xFFFF
                if ev == WM_LBUTTONUP:
                    self._open("/")
                elif ev == WM_RBUTTONUP:
                    self._menu()
                return 0
            if msg == self._msg_taskbar:          # o Explorer reiniciou: o ícone precisa ser recolocado
                self._add()
                return 0
            if msg == WM_DESTROY:
                self.remove()
                u32.PostQuitMessage(0)
                return 0
        except Exception:  # noqa: BLE001
            log.exception("ícone da área de notificação: erro ao tratar clique")
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ---------- menu ----------
    def _item(self, menu, text: str, fn=None, enabled: bool = True) -> None:
        cid = len(self._actions) + 100
        self._actions[cid] = fn
        u32.AppendMenuW(menu, MF_STRING | (0 if enabled and fn else MF_GRAYED), cid, text)

    def _menu(self) -> None:
        self._actions = {}
        su = getattr(self.ctx, "selfupdate", None)
        upd = su.status() if su else {}
        menu = u32.CreatePopupMenu()
        self._item(menu, f"Self Monitoring {__version__}: rodando")
        u32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        self._item(menu, "Abrir o painel", lambda: self._open("/"))
        self._item(menu, "Abrir o Mural", lambda: self._open("/#mural:abrir"))
        u32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        ips = self.lan_ips()
        for ip in ips:
            url = f"http://{ip}:{self.port}"
            self._item(menu, f"Na rede: {url}  (copiar)", lambda u=url: self._copy(u))
        self._item(menu, f"Neste PC: http://localhost:{self.port}  (copiar)", lambda: self._copy(f"http://localhost:{self.port}"))

        pc = u32.CreatePopupMenu()
        cfg = self.ctx.config
        self._item(pc, "Bloquear a tela", self._lock, cfg.allow_lock)
        self._item(pc, "Reiniciar o PC...", lambda: self._power("reboot"), cfg.allow_power)
        self._item(pc, "Desligar o PC...", lambda: self._power("poweroff"), cfg.allow_power)
        timer = self.ctx.power_timer.status() if getattr(self.ctx, "power_timer", None) else None
        if timer:
            self._item(pc, "Cancelar o desligamento agendado", self._cancel_timer)
        u32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        u32.AppendMenuW(menu, MF_POPUP, pc, "Computador")

        panel = u32.CreatePopupMenu()
        self._item(panel, "Procurar atualização", self._check_update, bool(su and su.enabled))
        if upd.get("state") == "update" and cfg.update_apply:
            self._item(panel, f"Atualizar agora para {upd.get('latest')}...", self._apply_update, bool(upd.get("can_apply")))
        u32.AppendMenuW(panel, MF_SEPARATOR, 0, None)
        self._item(panel, "Abrir a configuração (config.toml)", lambda: self._notepad(ROOT / "config.toml"))
        self._item(panel, "Ver o log", lambda: self._notepad(ROOT / "data" / "self-monitoring.log"))
        self._item(panel, "Abrir a pasta do painel", lambda: open_in_user_session(str(ROOT)))
        u32.AppendMenuW(panel, MF_SEPARATOR, 0, None)
        self._item(panel, "Encerrar o painel...", self._quit)
        u32.AppendMenuW(menu, MF_POPUP, panel, "Painel")

        pt = wintypes.POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        u32.SetForegroundWindow(self.hwnd)            # senão o menu não fecha ao clicar fora
        cmd = u32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY, pt.x, pt.y, 0, self.hwnd, None)
        u32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        u32.DestroyMenu(menu)
        fn = self._actions.get(cmd)
        if fn:
            threading.Thread(target=self._safe, args=(fn,), daemon=True).start()   # não trava o ícone

    @staticmethod
    def _safe(fn) -> None:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            log.exception("ação do ícone falhou")
            _toast("Não deu certo", str(e)[:200])

    # ---------- ações ----------
    def _open(self, path: str) -> None:
        open_in_user_session(f"http://localhost:{self.port}{path}")

    def _copy(self, url: str) -> None:
        if copy_text(self.hwnd, url):
            _toast("Endereço copiado", f"{url}\nCole no navegador do celular ou de outro PC da rede.")

    def _confirm(self, text: str) -> bool:
        return u32.MessageBoxW(None, text, "Self Monitoring", MB_YESNO | MB_ICONWARNING | MB_TOPMOST | MB_SETFOREGROUND) == IDYES

    def _lock(self) -> None:
        from .desktop import set_locked
        set_locked(True)

    def _power(self, action: str) -> None:
        from . import power
        verb = "Reiniciar" if action == "reboot" else "Desligar"
        if self._confirm(f"{verb} o PC agora? Programas abertos serão fechados."):
            power.request(action, False)

    def _cancel_timer(self) -> None:
        self.ctx.power_timer.cancel()
        _toast("Desligamento cancelado", "O desligamento agendado pelo painel foi cancelado.")

    def _check_update(self) -> None:
        su = self.ctx.selfupdate
        su.check_now()
        st = su.status()
        if st.get("error"):
            _toast("Não foi possível verificar", st["error"])
        elif st.get("state") == "update":
            _toast("Versão nova disponível", f"{st['latest']} (instalada: {st['installed']}). "
                   "Clique no ícone > Painel > Atualizar agora, ou use a aba Atualização.")
        else:
            _toast("Nenhuma versão nova", f"Você está na versão mais recente ({st['installed']}).")

    def _apply_update(self) -> None:
        st = self.ctx.selfupdate.status()
        if self._confirm(f"Atualizar o painel de {st['installed']} para {st['latest']}?\n\n"
                         "Ele baixa do GitHub e instala; o painel fica fora do ar por cerca de 1 minuto."):
            self.ctx.selfupdate.apply()
            _toast("Atualizando", "Uma janela mostra o andamento; o painel reinicia sozinho em cerca de 1 minuto.")

    @staticmethod
    def _notepad(path) -> None:
        subprocess.Popen(["notepad.exe", str(path)])

    def _quit(self) -> None:
        if self._confirm("Encerrar o painel? Ele volta no próximo logon (ou rodando o install.cmd).\n"
                         "Até lá, o celular não consegue acessar."):
            log.info("painel encerrado pelo ícone da área de notificação")
            self.remove()
            if getattr(self.ctx, "game", None):
                self.ctx.game.stop()              # não deixa o PresentMon rodando sozinho
            os._exit(0)


def _toast(title: str, body: str) -> None:
    from .desktop import notify_pc
    notify_pc(title, body)
