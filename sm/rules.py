"""Regras que classificam eventos do log do Windows em eventos do painel (sem IA: fonte, ID do evento e texto).

Mesma ideia e mesmo formato de saída da versão Linux (que lê o journal). Cada regra diz:
  kind      tipo do evento (ex.: gpu_reset)
  family    grupo usado para juntar eventos num incidente (gpu, crash, memory, service, thermal, storage,
            hardware, kernel, power)
  severity  info / notice / warning / error / critical
  trigger   se o evento sozinho abre um incidente
  label     texto curto em português

Para adicionar uma regra: acrescente um Rule em RULES. A primeira que casar vence, então regras mais
específicas vêm antes. `classify(m)` recebe a linha já normalizada por eventlog.normalize (com `data`).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

GPU_DRIVERS = r"^(nvlddmkm|amdkmdag|amdkmdap|amdwddmg|amduw23g|igfx|igfxn|igdkmd64|igdkmdn64|igdkmdnd64|Display)$"


@dataclass
class Rule:
    kind: str
    family: str
    severity: str
    label: str
    provider: str | None = None          # regex na fonte (provider ou nome antigo da fonte)
    ids: set[int] | None = None          # IDs do evento
    pattern: str | None = None           # regex no texto (mensagem + dados)
    trigger: bool = False
    rx: re.Pattern | None = field(default=None, init=False, repr=False)
    prx: re.Pattern | None = field(default=None, init=False, repr=False)

    def __post_init__(self):
        self.rx = re.compile(self.pattern, re.I) if self.pattern else None
        self.prx = re.compile(self.provider, re.I) if self.provider else None


RULES: list[Rule] = [
    # ---------- tela azul / desligamento ----------
    Rule("bugcheck", "kernel", "critical", "Tela azul (BSOD)", trigger=True,
         provider=r"^(Microsoft-Windows-WER-SystemErrorReporting|BugCheck)$", ids={1001}),
    # tratados pela verificação do boot anterior (incidents.check_previous_boot): só registram
    Rule("unexpected_shutdown_log", "power", "critical", "Desligamento inesperado registrado",
         provider=r"^Microsoft-Windows-Kernel-Power$", ids={41}),
    Rule("unexpected_shutdown_log", "power", "critical", "Desligamento inesperado registrado",
         provider=r"^EventLog$", ids={6008}),
    # ---------- GPU ----------
    Rule("gpu_reset", "gpu", "critical", "Driver de vídeo parou de responder e foi recuperado (TDR)", trigger=True,
         provider=r"^Display$", ids={4101}),
    Rule("gpu_reset", "gpu", "critical", "Driver de vídeo reiniciou a GPU", trigger=True,
         provider=r"^(nvlddmkm)$", ids={153, 14}),
    Rule("gpu_hang", "gpu", "critical", "GPU travou (Xid / watchdog)", trigger=True,
         provider=r"^(nvlddmkm)$", ids={13}),
    Rule("gpu_hang", "gpu", "critical", "GPU travou (watchdog do Windows)", trigger=True,
         provider=r"^Windows Error Reporting$", ids={1001}, pattern=r"LiveKernelEvent.*P1: (141|117|1a1|1a8|193)\b"),
    Rule("gpu_driver", "gpu", "warning", "Erro do driver de vídeo", provider=GPU_DRIVERS),
    # ---------- travamentos de programas ----------
    Rule("coredump", "crash", "error", "Programa travou", trigger=True, provider=r"^Application Error$", ids={1000}),
    Rule("app_hang", "crash", "warning", "Programa parou de responder e foi fechado", provider=r"^Application Hang$", ids={1002}),
    Rule("dotnet_crash", "crash", "error", "Erro .NET sem tratamento", provider=r"^\.NET Runtime$", ids={1026}),
    # ---------- memória ----------
    Rule("oom", "memory", "critical", "Memória virtual esgotada: o Windows pediu para fechar programas", trigger=True,
         provider=r"^Microsoft-Windows-Resource-Exhaustion-Detector$", ids={2004}),
    # ---------- hardware ----------
    Rule("mce", "hardware", "critical", "Erro de hardware fatal (WHEA)", trigger=True,
         provider=r"^Microsoft-Windows-WHEA-Logger$", ids={1, 18, 20, 46}),
    Rule("pcie_aer", "hardware", "warning", "Erro corrigido no barramento PCIe (WHEA)",
         provider=r"^Microsoft-Windows-WHEA-Logger$", ids={17}),
    Rule("mce_corrected", "hardware", "warning", "Erro de hardware corrigido (WHEA)",
         provider=r"^Microsoft-Windows-WHEA-Logger$", ids={19, 47}),
    Rule("usb", "hardware", "notice", "Driver de dispositivo não carregou", provider=r"^Microsoft-Windows-Kernel-PnP$", ids={219}),
    # ---------- disco ----------
    Rule("disk_io", "storage", "critical", "Erro de leitura/escrita no disco", trigger=True,
         provider=r"^(disk|Disk)$", ids={7, 11, 51, 153}),
    Rule("disk_io", "storage", "critical", "Disco parou de responder e foi reiniciado", trigger=True,
         provider=r"^(stornvme|storahci|iaStorAC|iaStorA|amdsata|nvme)$", ids={129, 11}),
    Rule("disk_io", "storage", "critical", "Sistema de arquivos corrompido (NTFS)", trigger=True,
         provider=r"^(Ntfs|Microsoft-Windows-Ntfs)$", ids={55, 98, 137, 140}),
    # ---------- temperatura / energia ----------
    Rule("thermal", "thermal", "warning", "Processador limitado pelo firmware (temperatura/energia)",
         provider=r"^Microsoft-Windows-Kernel-Processor-Power$", ids={37}),
    Rule("thermal", "thermal", "critical", "Temperatura crítica (ACPI)", trigger=True,
         provider=r"^(ACPI|Microsoft-Windows-ACPI)$", ids={13, 4}),
    # ---------- serviços ----------
    Rule("unit_failed", "service", "error", "Serviço terminou inesperadamente", trigger=True,
         provider=r"^Service Control Manager$", ids={7031, 7034, 7023, 7024}),
    Rule("unit_failed", "service", "error", "Serviço não iniciou",
         provider=r"^Service Control Manager$", ids={7000, 7001, 7009, 7011, 7022, 7038, 7043}),
    # ---------- sessão gráfica ----------
    Rule("compositor", "session", "error", "Área de trabalho (Explorer/DWM) caiu", trigger=False,
         provider=r"^(Application Error|Application Hang)$", ids={1000, 1002}, pattern=r"\b(explorer|dwm)\.exe\b"),
]
# a regra da área de trabalho é mais específica que a de programa travado: vem antes
RULES.insert(0, RULES.pop(next(i for i, r in enumerate(RULES) if r.kind == "compositor")))

BOOT_GRACE = 120    # s depois do início do Windows em que avisos não abrem incidente
PRIO_SEVERITY = {0: "critical", 1: "critical", 2: "critical", 3: "error", 4: "warning", 5: "notice", 6: "info", 7: "info"}
EXCEPTIONS = {
    "c0000005": "acesso inválido à memória", "c0000409": "estouro de buffer na pilha / encerramento rápido (fail-fast)",
    "c0000374": "memória do heap corrompida", "c00000fd": "estouro de pilha", "80000003": "ponto de parada (assert/debug)",
    "c000001d": "instrução inválida", "e06d7363": "exceção C++ sem tratamento", "c0000142": "falha ao iniciar uma DLL",
    "c0000135": "DLL não encontrada", "c0000417": "parâmetro inválido (runtime C)", "c0000602": "falha imediata (fail fast)",
    "c0000006": "erro de página (arquivo/disco indisponível)",
    "40000015": "encerrado pelo próprio programa (abort)", "c0000094": "divisão por zero", "c0000096": "instrução privilegiada",
    "c0000194": "deadlock (seção crítica)", "c0000420": "falha de asserção",
}
CRASH_FIELDS = ["AppName", "AppVersion", "AppTimeStamp", "ModuleName", "ModuleVersion", "ModuleTimeStamp", "ExceptionCode",
                "FaultingOffset", "ProcessId", "ProcessCreationTime", "AppPath", "ModulePath", "IntegratorReportId"]


def is_kernel(m: dict) -> bool:
    return False


def text(v) -> str:
    return str(v or "")


def _boot_age(ts: float) -> float:
    """Segundos entre o início do Windows (o mais recente antes do evento) e o evento."""
    try:
        from . import eventlog
        for b in eventlog.boots():
            if b["first"] <= ts:
                return ts - b["first"]
    except Exception:  # noqa: BLE001
        pass
    return 1e9


def crash_data(data: dict) -> dict:
    """Campos do 'Application Error' 1000 (com nome nas versões novas; param1..12 nas antigas)."""
    if "AppName" not in data:
        vals = [data.get(f"param{i}") for i in range(1, 14)]
        data = {k: v for k, v in zip(CRASH_FIELDS, vals) if v is not None}
    code = (data.get("ExceptionCode") or "").lower().removeprefix("0x")
    try:
        pid = int(data.get("ProcessId") or "0", 16 if str(data.get("ProcessId", "")).startswith("0x") else 10) or None
    except ValueError:
        pid = None
    return {"exe": data.get("AppPath") or data.get("AppName"), "comm": data.get("AppName"), "app_version": data.get("AppVersion"),
            "module": data.get("ModuleName"), "module_version": data.get("ModuleVersion"), "module_path": data.get("ModulePath"),
            "code": code or None, "signal": f"0x{code}" if code else None, "meaning": EXCEPTIONS.get(code),
            "offset": data.get("FaultingOffset"), "core_pid": pid, "report": data.get("IntegratorReportId")}


_svc_names: tuple[float, dict] = (0.0, {})


def service_name(display: str | None) -> str | None:
    """Nome de exibição (o que o Gerenciador de Serviços grava) -> nome curto do serviço."""
    global _svc_names
    if not display:
        return None
    if time.time() - _svc_names[0] > 600:
        try:
            from .services import enum_services
            _svc_names = (time.time(), {s["display"].lower(): s["name"] for s in enum_services()} |
                          {s["name"].lower(): s["name"] for s in enum_services()})
        except OSError:
            _svc_names = (time.time(), {})
    return _svc_names[1].get(display.lower(), display)


def dump_time(path: str | None) -> float | None:
    """WATCHDOG-20260917-1816.dmp -> horário (local) em que o despejo foi gravado."""
    mm = re.search(r"-(\d{8})-(\d{4})\.dmp$", path or "", re.I)
    if not mm:
        return None
    try:
        return time.mktime(time.strptime(mm.group(1) + mm.group(2), "%Y%m%d%H%M"))
    except ValueError:
        return None


def classify(m: dict) -> dict | None:
    """Linha do log de eventos -> evento classificado, ou None se nenhuma regra casar.
    Erros e críticos sem regra viram "error" genérico, sem abrir incidente."""
    prov = m.get("provider") or ""
    ident = m.get("ident") or prov
    eid = m.get("event_id")
    msg = text(m.get("msg"))
    blob = msg + " " + " ".join(f"{k}: {v}" for k, v in (m.get("data") or {}).items())
    for r in RULES:
        if r.prx and not (r.prx.search(prov) or r.prx.search(ident)):
            continue
        if r.ids and eid not in r.ids:
            continue
        if r.rx and not r.rx.search(blob):
            continue
        if not r.prx and not r.ids and not r.rx:
            continue
        if r.kind == "gpu_driver" and m.get("prio", 6) > 3:
            continue                         # do driver de vídeo, só erros
        ev = _event(m, r.kind, r.family, r.severity, r.trigger, r.label, msg, ident)
        if ev["trigger"] and r.family not in ("storage", "hardware", "kernel") and _boot_age(ev["ts"]) < BOOT_GRACE:
            ev["trigger"] = False            # drivers e serviços iniciando costumam reclamar no boot
            ev["data"]["boot_noise"] = True
        return ev
    if m.get("prio", 6) <= 3:
        return _event(m, "error", "other", PRIO_SEVERITY[m["prio"]], False, "Erro registrado no Windows", msg, ident)
    return None


def _event(m: dict, kind: str, family: str, severity: str, trigger: bool, label: str, msg: str, ident: str) -> dict:
    d = m.get("data") or {}
    data: dict = {"label": label, "priority": m.get("prio"), "cursor": m.get("cursor"), "event_id": m.get("event_id"),
                  "channel": m.get("channel")}
    pid = m.get("pid")
    unit = None
    if kind in ("coredump", "compositor", "app_hang"):
        c = crash_data(d)
        data.update(c)
        pid = c.get("core_pid") or pid
        first = f"{c.get('comm') or 'programa'} travou"
        if c.get("signal"):
            first += f" ({c['signal']}{': ' + c['meaning'] if c.get('meaning') else ''})"
        if c.get("module"):
            first += f" no módulo {c['module']}"
        msg = first if kind != "app_hang" else f"{d.get('param1') or d.get('AppName') or 'programa'} parou de responder"
    elif kind == "unit_failed":
        display = d.get("param1")
        unit = service_name(display)
        data["display"] = display
        data["result"] = {7031: "crash", 7034: "crash", 7023: "exit-code", 7024: "exit-code", 7000: "start-failed",
                          7001: "dependency", 7009: "timeout", 7011: "timeout", 7022: "timeout", 7038: "logon",
                          7043: "timeout"}.get(m.get("event_id"), "failed")
    elif kind == "bugcheck":
        mm = re.search(r"0x[0-9a-f]{8}", msg, re.I)
        data["bugcheck"] = (d.get("param1") or (mm.group(0) if mm else "")) or None
        data["dump"] = d.get("param2")
    elif kind == "gpu_hang" and ident == "Windows Error Reporting":
        data["live_kernel_event"] = d.get("P1")
        dump = (d.get("AttachedFiles") or "").split("\n")[0].removeprefix("\\\\?\\") or None
        data["dump"] = dump
        when = dump_time(dump)
        data["happened"] = when
        msg = f"Watchdog da GPU (LiveKernelEvent {d.get('P1')}): o driver de vídeo travou"
        if when:
            msg += time.strftime(" em %d/%m/%Y %H:%M", time.localtime(when))
        # o Windows reenvia relatórios antigos (ex.: no logon): só o que acabou de acontecer abre incidente
        if when and abs((m.get("ts") or 0) - when) > 600:
            trigger = False
            data["old_report"] = True
            msg += " (relatório antigo, reenviado agora)"
    return {"ts": m.get("ts") or 0.0, "source": "eventlog", "kind": kind, "family": family, "severity": severity,
            "trigger": trigger, "unit": unit, "ident": ident or None, "pid": pid, "boot": None,
            "message": (msg or label)[:2000], "data": data}
