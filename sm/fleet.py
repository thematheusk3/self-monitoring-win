"""Identidade da máquina e formato de envio para o futuro servidor central (várias máquinas).

Hoje: cada instalação tem um `machine` estável (derivado do MachineGuid do Windows, sem expor o valor original)
e o painel monta "pacotes" com o que mudou no banco (store.changes). O envio ainda não existe: o desenho está no
FROTA.md da versão Linux (o formato é o mesmo nas duas; o servidor diferencia pelo campo `os`).
"""
from __future__ import annotations

import hashlib
import platform
import socket
import time
import winreg

from . import __version__

SCHEMA = 1          # versão do formato do pacote; o servidor recusa o que não conhece
_ID: str | None = None


def machine_id() -> str:
    """16 hex estáveis por instalação do Windows. Hash com prefixo: não dá para voltar ao MachineGuid."""
    global _ID
    if _ID is None:
        raw = ""
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                raw = str(winreg.QueryValueEx(k, "MachineGuid")[0]).strip()
        except OSError:
            pass
        if not raw:
            raw = f"{socket.gethostname()}-{platform.machine()}"
        _ID = hashlib.sha256(f"self-monitoring:{raw}".encode()).hexdigest()[:16]
    return _ID


def identity() -> dict:
    return {"machine": machine_id(), "hostname": socket.gethostname(), "os": "windows",
            "kernel": platform.version(), "arch": platform.machine(), "version": __version__}


def package(store, since: int, limit: int = 1000) -> dict:
    """Pacote no formato que um agente mandaria ao servidor central (POST /api/fleet/ingest, futuro)."""
    return {"schema": SCHEMA, "sent": time.time(), "identity": identity(), **store.changes(since, limit)}
