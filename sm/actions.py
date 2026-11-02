"""Ações que alteram o sistema. Mantidas separadas dos coletores (leitura)."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

import psutil

# processos cujo encerramento derruba o Windows (tela azul) ou a sessão
CRITICAL = {"csrss.exe", "wininit.exe", "winlogon.exe", "smss.exe", "services.exe", "lsass.exe", "lsaiso.exe",
            "system", "registry", "secure system", "memory compression", "dwm.exe", "fontdrvhost.exe"}
WM_CLOSE = 0x0010
SIGNALS = ("TERM", "KILL", "STOP", "CONT")


class ActionError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def close_windows(pid: int) -> int:
    """Pede para o processo fechar como se clicassem no X (WM_CLOSE nas janelas dele).
    É o equivalente ao SIGTERM: o programa pode perguntar se quer salvar."""
    user32 = ctypes.windll.user32
    hwnds = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):   # GW_OWNER
            hwnds.append(hwnd)
        return True
    user32.EnumWindows(cb, 0)
    for h in hwnds:
        user32.PostMessageW(h, WM_CLOSE, 0, 0)
    return len(hwnds)


def signal_process(pid: int, sig_name: str, create_time: float | None) -> dict:
    sig = sig_name.upper()
    if sig not in SIGNALS:
        raise ActionError(f"Sinal inválido: {sig_name}")
    if pid in (0, 4) or pid == os.getpid():
        raise ActionError("Este processo é protegido.", 403)
    try:
        p = psutil.Process(pid)
        # evita matar outro processo que reaproveitou o mesmo PID (no Windows os PIDs são reusados rápido)
        if create_time is not None and abs(p.create_time() - float(create_time)) > 1:
            raise ActionError("O processo já terminou (PID reaproveitado).", 409)
        name = p.name()
        if name.lower() in CRITICAL:
            raise ActionError(f"{name} é um processo crítico do Windows: finalizá-lo trava ou reinicia o PC.", 403)
        note = None
        if sig == "TERM":
            if close_windows(pid) == 0:      # sem janela (serviço, processo de fundo): só dá para encerrar
                p.terminate()
                note = "O processo não tem janela; foi encerrado direto."
        elif sig == "KILL":
            p.kill()
        elif sig == "STOP":
            p.suspend()
        else:
            p.resume()
    except psutil.NoSuchProcess:
        raise ActionError("Processo não existe mais.", 404) from None
    except psutil.AccessDenied:
        raise ActionError("Permissão negada: o processo é protegido pelo Windows ou de outro usuário. "
                          "Rode o painel como administrador (instalador padrão) para gerenciá-lo.", 403) from None
    out = {"ok": True, "pid": pid, "name": name, "signal": sig}
    if note:
        out["note"] = note
    return out
