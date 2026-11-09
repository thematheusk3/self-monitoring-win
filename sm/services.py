"""Serviços do Windows: estado, consumo e ações (mesma API da versão Linux, que lista as unidades do systemd).

Lista: EnumServicesStatusExW (uma chamada traz nome, estado, PID e código de saída de todos). O modo de início
(automático/manual/desativado) vem do QueryServiceConfig, guardado por 60 s. CPU, memória e threads são as do
processo do serviço, tiradas da lista de processos do painel; vários serviços podem dividir o mesmo svchost.exe,
e aí o consumo é do grupo (campo `shared`).

Escopo "system" = serviços do Windows; "user" = instâncias dos serviços por usuário (ex.: CDPUserSvc_1a2b3).
"Com falha" = parado com código de saída de erro, ou automático que não está rodando por erro.
Ações (iniciar/parar/reiniciar) pelo próprio Gerenciador de Serviços; o painel roda como administrador.
"""
from __future__ import annotations

import ctypes
import re
import threading
import time
from ctypes import wintypes

import psutil

from .win import powershell_json, ps_quote

RX_NAME = re.compile(r"[\w.$@+\-]{1,256}")
ACTIONS = {"start", "stop", "restart"}
# o Windows já recusa parar a maioria destes; o painel nem tenta
PROTECTED = {"rpcss", "rpceptmapper", "dcomlaunch", "lsm", "samss", "plugplay", "power", "brokerinfrastructure",
             "systemeventsbroker", "eventlog", "schedule", "winmgmt", "profsvc", "coremessagingregistrar", "audiosrv",
             "audioendpointbuilder", "windefend", "mpssvc", "wscsvc", "sense", "bfe"}

SC_MANAGER_ENUMERATE_SERVICE = 0x0004
SERVICE_QUERY_CONFIG = 0x0001
SC_ENUM_PROCESS_INFO = 0
SERVICE_STATE_ALL = 3
SERVICE_WIN32 = 0x30
SERVICE_USER_SERVICE = 0x40
SERVICE_USERSERVICE_INSTANCE = 0x80
ERROR_MORE_DATA = 234
STATES = {1: ("inactive", "dead"), 2: ("activating", "start-pending"), 3: ("deactivating", "stop-pending"),
          4: ("active", "running"), 5: ("activating", "continue-pending"), 6: ("deactivating", "pause-pending"),
          7: ("active", "paused")}
OK_EXIT = {0, 1077}      # 1077 = ERROR_SERVICE_NEVER_STARTED
ERROR_SERVICE_SPECIFIC = 1066


class _SSP(ctypes.Structure):
    _fields_ = [("dwServiceType", wintypes.DWORD), ("dwCurrentState", wintypes.DWORD),
                ("dwControlsAccepted", wintypes.DWORD), ("dwWin32ExitCode", wintypes.DWORD),
                ("dwServiceSpecificExitCode", wintypes.DWORD), ("dwCheckPoint", wintypes.DWORD),
                ("dwWaitHint", wintypes.DWORD), ("dwProcessId", wintypes.DWORD), ("dwServiceFlags", wintypes.DWORD)]


class _ESSP(ctypes.Structure):
    _fields_ = [("lpServiceName", wintypes.LPWSTR), ("lpDisplayName", wintypes.LPWSTR), ("ServiceStatusProcess", _SSP)]


def enum_services() -> list[dict]:
    adv = ctypes.windll.advapi32
    adv.OpenSCManagerW.restype = wintypes.HANDLE
    scm = adv.OpenSCManagerW(None, None, SC_MANAGER_ENUMERATE_SERVICE)
    if not scm:
        raise OSError(ctypes.get_last_error(), "OpenSCManager falhou")
    try:
        types = SERVICE_WIN32 | SERVICE_USER_SERVICE | SERVICE_USERSERVICE_INSTANCE
        need, count, resume = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
        adv.EnumServicesStatusExW(wintypes.HANDLE(scm), SC_ENUM_PROCESS_INFO, types, SERVICE_STATE_ALL, None, 0,
                                  ctypes.byref(need), ctypes.byref(count), ctypes.byref(resume), None)
        out = []
        resume = wintypes.DWORD()
        while True:
            size = need.value + 4096
            buf = ctypes.create_string_buffer(size)
            ok = adv.EnumServicesStatusExW(wintypes.HANDLE(scm), SC_ENUM_PROCESS_INFO, types, SERVICE_STATE_ALL, buf,
                                           size, ctypes.byref(need), ctypes.byref(count), ctypes.byref(resume), None)
            arr = ctypes.cast(buf, ctypes.POINTER(_ESSP))
            for i in range(count.value):
                e = arr[i]
                s = e.ServiceStatusProcess
                out.append({"name": e.lpServiceName, "display": e.lpDisplayName, "type": s.dwServiceType,
                            "state": s.dwCurrentState, "pid": s.dwProcessId or None,
                            "exit": s.dwServiceSpecificExitCode if s.dwWin32ExitCode == ERROR_SERVICE_SPECIFIC
                            else s.dwWin32ExitCode})
            if ok or ctypes.GetLastError() != ERROR_MORE_DATA:
                break
        return out
    finally:
        adv.CloseServiceHandle(wintypes.HANDLE(scm))


_cfg_lock = threading.Lock()
_cfg: dict[str, str | None] = {}
_cfg_at = 0.0


def _start_types(names: list[str]) -> dict[str, str | None]:
    """Modo de início de cada serviço (muda pouco: guardado por 60 s)."""
    global _cfg_at
    with _cfg_lock:
        if time.time() - _cfg_at > 60 or any(n not in _cfg for n in names):
            for n in names:
                try:
                    _cfg[n] = psutil.win_service_get(n).start_type()
                except (psutil.Error, OSError):
                    _cfg[n] = None
            _cfg_at = time.time()
        return dict(_cfg)


def list_units(user: bool = False, processes: list[dict] | None = None) -> dict:
    try:
        svcs = enum_services()
        err = None
    except OSError as e:
        svcs, err = [], str(e)
    if user:
        svcs = [s for s in svcs if s["type"] & SERVICE_USERSERVICE_INSTANCE]
    else:
        svcs = [s for s in svcs if not s["type"] & SERVICE_USER_SERVICE]
    starts = _start_types([s["name"] for s in svcs])
    procs = {p["pid"]: p for p in processes or []}
    by_pid: dict[int, int] = {}
    for s in svcs:
        if s["pid"]:
            by_pid[s["pid"]] = by_pid.get(s["pid"], 0) + 1
    out = []
    for s in svcs:
        active, sub = STATES.get(s["state"], ("unknown", "unknown"))
        start = starts.get(s["name"])
        failed = s["state"] == 1 and s["exit"] not in OK_EXIT
        p = procs.get(s["pid"]) if s["pid"] else None
        out.append({
            "unit": s["name"], "description": s["display"], "load": "loaded",
            "active": "failed" if failed else active, "sub": "failed" if failed else sub,
            "enabled": start, "pid": s["pid"],
            "mem": p.get("mem") if p else None, "cpu": p.get("cpu") if p else None, "cpu_total": None,
            "tasks": p.get("threads") if p else None, "restarts": None, "result": None,
            "since": p.get("create_time") if p else None, "exit": s["exit"] if s["exit"] not in OK_EXIT else None,
            "type": "share" if s["type"] & 0x20 else "own", "shared": by_pid.get(s["pid"], 0) if s["pid"] else 0,
            "self": False,
        })
    counts = {"total": len(out), "running": sum(1 for x in out if x["sub"] == "running"),
              "failed": sum(1 for x in out if x["active"] == "failed")}
    return {"scope": "user" if user else "system", "units": out, "counts": counts, "ts": time.time(), "error": err}


def valid_unit(name: str) -> bool:
    return bool(RX_NAME.fullmatch(name or ""))


def detail(name: str, user: bool = False) -> dict:
    from . import eventlog
    if not valid_unit(name):
        raise ValueError("Serviço inválido.")
    q = ps_quote(name)
    d = powershell_json(
        f"$s = Get-CimInstance Win32_Service -Filter \"Name={q}\";"
        f"$g = Get-Service -Name {q} -ErrorAction SilentlyContinue;"
        "if ($s) { [pscustomobject]@{Name=$s.Name; DisplayName=$s.DisplayName; Description=$s.Description;"
        " State=$s.State; StartMode=$s.StartMode; DelayedAutoStart=$s.DelayedAutoStart; StartName=$s.StartName;"
        " PathName=$s.PathName; ProcessId=$s.ProcessId; ExitCode=$s.ExitCode;"
        " ServiceSpecificExitCode=$s.ServiceSpecificExitCode; ServiceType=$s.ServiceType;"
        " DependsOn=@($g.ServicesDependedOn | ForEach-Object { $_.Name });"
        " Dependents=@($g.DependentServices | ForEach-Object { $_.Name }) } }", timeout=20)
    d = d[0] if d else {}
    if not d:
        raise ValueError("Serviço não encontrado.")
    since = None
    if d.get("ProcessId"):
        try:
            since = psutil.Process(int(d["ProcessId"])).create_time()
        except (psutil.Error, ValueError):
            pass
    logs = eventlog.service_logs(name, d.get("DisplayName"), 80)
    return {"unit": name, "scope": "user" if user else "system", "props": d, "since": since, "logs": logs,
            "self": False, "protected": name.lower() in PROTECTED}


def action(name: str, act: str, user: bool = False) -> dict:
    if not valid_unit(name):
        raise ValueError("Serviço inválido.")
    if act not in ACTIONS:
        raise ValueError("Ação inválida.")
    if act in ("stop", "restart") and name.lower() in PROTECTED:
        raise PermissionError("Este serviço é essencial para o Windows (ou para o painel) e não é parado pelo painel.")
    cmd = {"start": "Start-Service", "stop": "Stop-Service", "restart": "Restart-Service"}[act]
    r = powershell_json(
        f"try {{ {cmd} -Name {ps_quote(name)} -ErrorAction Stop -WarningAction SilentlyContinue; "
        "[pscustomobject]@{ok=$true} } catch { [pscustomobject]@{ok=$false; error=$_.Exception.Message} }", timeout=90)
    r = r[0] if r else None
    if not r or not r.get("ok"):
        err = (r or {}).get("error") or f"{cmd} falhou"
        if re.search(r"dependent|depend", err, re.I):
            raise RuntimeError(f"{err} (outros serviços dependem dele; pare-os antes)")
        if re.search(r"acesso negado|access is denied|access denied", err, re.I):
            raise PermissionError(err)
        raise RuntimeError(err[:400])
    return {"ok": True, "unit": name, "action": act}
