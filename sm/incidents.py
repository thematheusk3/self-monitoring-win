"""Investigador de incidentes: correlação de eventos + regras, sem IA (mesmo desenho da versão Linux).

Fluxo:
  1. Um evento "gatilho" (rules.py: driver de vídeo reiniciado/GPU travada, programa que travou, memória
     esgotada, serviço que caiu, erro de disco, erro de hardware WHEA, tela azul) abre um incidente. Gatilhos
     próximos (2 min) de famílias relacionadas entram no mesmo incidente (ex.: a GPU travou e, 3 s depois, o jogo caiu).
  2. Espera alguns segundos para juntar o que vem depois e monta o relatório:
     - linha do tempo: eventos classificados, ações feitas pelo painel e atualizações do Windows, na janela
       (5 min antes até 1 min depois);
     - métricas em volta (CPU, temperaturas, GPU, memória, FPS): antes, no momento e pico;
     - processos e jogo no momento (se o incidente é ao vivo);
     - o programa/serviço envolvido (executável, código da exceção, módulo com falha, tempo rodando);
     - mudanças recentes (Windows Update, driver de vídeo, BIOS, hardware);
     - hipóteses com confiança, evidências e o que verificar;
     - um texto em Markdown pronto para colar num chat com uma IA.
  3. Na partida, verifica se o Windows anterior terminou sem desligamento normal (Kernel-Power 41 / EventLog 6008:
     queda de energia, travamento total, tela azul) e abre um incidente com as últimas linhas daquele período.
Também dá para pedir uma investigação manual de um momento (aba Logs/Incidentes).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import threading
import time
import uuid
from collections import Counter

import psutil

from . import eventlog, fleet, hwinfo

log = logging.getLogger(__name__)
PRE = 300            # s antes do gatilho na janela do relatório
POST = 60            # s depois do último gatilho
SETTLE = 25          # s esperando eventos que vêm depois, antes de montar o relatório
GROUP = 120          # s: gatilhos próximos entram no mesmo incidente
REPEAT = 15 * 60     # s: o mesmo programa/serviço caindo de novo vira repetição do mesmo incidente
RELATED = {"gpu": {"gpu", "crash", "session", "kernel"}, "crash": {"gpu", "crash", "memory", "session"},
           "memory": {"memory", "crash", "service"}, "service": {"service", "memory"},
           "kernel": {"kernel", "gpu", "hardware"}, "hardware": {"hardware", "kernel", "storage"},
           "storage": {"storage", "hardware"}, "thermal": {"thermal", "gpu", "crash"}, "session": {"session", "gpu", "crash"}}
KIND_RANK = ["bugcheck", "gpu_reset", "gpu_hang", "mce", "kernel_bug", "disk_io", "oom", "thermal", "gpu_fault",
             "unexpected_shutdown", "coredump", "unit_failed", "manual"]
SEV = {"info": 0, "notice": 1, "warning": 2, "error": 3, "critical": 4}
# arquivos de overlay/injeção que, alterados com o jogo aberto, costumam derrubar o jogo
OVERLAY_FILES = ["%ProgramFiles(x86)%/RivaTuner Statistics Server/Profiles/Global",
                 "%ProgramFiles(x86)%/MSI Afterburner/Profiles/MSIAfterburner.cfg"]
RELEVANT_PKGS = re.compile(r".*")       # no Windows, toda atualização instalada pode importar (driver, .NET, cumulativa)
NOISE = re.compile(r"^As configurações de permissão específico do aplicativo|application-specific permission settings", re.I)
METRICS = {   # chave -> (rótulo, unidade)
    "cpu.total": ("Uso da CPU", "%"), "cpu.temp": ("Temperatura da CPU", "°C"), "cpu.freq": ("Clock da CPU", "MHz"),
    "memory.percent": ("Memória usada", "%"), "memory.swap": ("Arquivo de paginação usado", "%"),
    "gpu.0.busy": ("Uso da GPU", "%"), "gpu.0.temp": ("Temperatura da GPU", "°C"), "gpu.0.vram": ("VRAM usada", "%"),
    "gpu.0.gtt": ("Memória compartilhada da GPU", "%"), "gpu.0.power": ("Consumo da GPU", "W"), "gpu.0.clock": ("Clock da GPU", "MHz"),
    "gpu.1.busy": ("Uso da GPU 2", "%"), "game.fps": ("FPS", ""), "disks.read": ("Leitura de disco", "B/s"),
    "disks.write": ("Escrita de disco", "B/s"),
}
TITLES = {
    "gpu_reset": "Driver de vídeo parou de responder e reiniciou a GPU", "gpu_hang": "GPU travou (erro do driver de vídeo)",
    "gpu_fault": "Acesso inválido à memória da GPU", "oom": "Memória virtual esgotada",
    "kernel_bug": "Falha no kernel", "mce": "Erro de hardware (WHEA)", "disk_io": "Erro de leitura/escrita no disco",
    "bugcheck": "Tela azul (BSOD)",
    "thermal": "Temperatura crítica", "unexpected_shutdown": "Desligamento inesperado",
    "manual": "Investigação manual",
}


def _sev_max(a: str, b: str) -> str:
    return a if SEV.get(a, 0) >= SEV.get(b, 0) else b


def _rank(kind: str) -> int:
    return KIND_RANK.index(kind) if kind in KIND_RANK else len(KIND_RANK)


def _hm(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _when(ts: float) -> str:
    return time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(ts))


class Investigator:
    def __init__(self, ctx, store) -> None:
        self.ctx = ctx
        self.store = store
        self.lock = threading.Lock()
        self.pending: dict[int, dict] = {}     # id -> {first, last, due, events, family, live_ctx}
        self._stop = threading.Event()
        self.audit = None                        # sm.audit.Audit (definido pelo servidor)

    # ---------- entrada de eventos ----------
    def on_event(self, ev: dict, eid: int) -> None:
        if not ev.get("trigger"):
            return
        now = time.time()
        with self.lock:
            for iid, p in self.pending.items():
                if abs(ev["ts"] - p["last"]) <= GROUP and ev["family"] in RELATED.get(p["family"], {p["family"]}):
                    p["events"].append((ev, eid))
                    p["last"] = max(p["last"], ev["ts"])
                    p["due"] = max(p["due"], self._due(ev["ts"], now))
                    if _rank(ev["kind"]) < _rank(p["kind"]):
                        p["kind"], p["family"] = ev["kind"], ev["family"]
                    p["severity"] = _sev_max(p["severity"], ev["severity"])
                    self.store.link_events([eid], iid)
                    return
        subject = self._subject_key(ev)
        rep = self._recent_same(subject, ev["ts"])
        if rep:
            self.store.link_events([eid], rep["id"])
            report = (self.store.incident(rep["id"]) or {}).get("report") or {}
            report.setdefault("repeats", []).append(ev["ts"])
            self.store.update_incident(rep["id"], count=rep["count"] + 1, report=report)
            return
        live = ev["ts"] > now - 60
        inc = {"uid": uuid.uuid4().hex[:12], "opened": now, "trigger_ts": ev["ts"], "family": ev["family"],
               "kind": ev["kind"], "severity": ev["severity"], "status": "analyzing",
               "title": self._title(ev["kind"], [ev]), "subject": subject}
        iid = self.store.add_incident(inc)
        self.store.link_events([eid], iid)
        with self.lock:
            self.pending[iid] = {"first": ev["ts"], "last": ev["ts"], "due": self._due(ev["ts"], now),
                                 "events": [(ev, eid)], "family": ev["family"], "kind": ev["kind"],
                                 "severity": ev["severity"], "live": self._live_context() if live else None}
        if live and self.ctx.alerts:
            self.ctx.alerts.event("incident", f"{inc['title']} — abra Incidentes para ver a análise.",
                                  "critical" if ev["severity"] == "critical" else "warning", key=inc["uid"])

    @staticmethod
    def _due(ts: float, now: float) -> float:
        return max(ts + SETTLE, now + 3)

    def _subject_key(self, ev: dict) -> str | None:
        d = ev.get("data") or {}
        if ev["kind"] == "coredump":
            return "exe:" + _proc_name(d)
        if ev["kind"] == "unit_failed":
            return "unit:" + (ev.get("unit") or "?")
        if ev["family"] == "gpu":
            return "gpu"
        return f"{ev['family']}:{ev['kind']}"

    def _recent_same(self, subject: str | None, ts: float) -> dict | None:
        if not subject:
            return None
        for inc in self.store.incidents(limit=30):
            if inc["subject"] == subject and 0 <= ts - inc["trigger_ts"] <= REPEAT and inc["id"] not in self.pending:
                return inc
        return None

    def _live_context(self) -> dict:
        procs = list(self.ctx.processes or [])
        top_cpu = sorted(procs, key=lambda p: p.get("cpu") or 0, reverse=True)[:8]
        top_mem = sorted(procs, key=lambda p: p.get("mem") or 0, reverse=True)[:8]
        keep = ("pid", "name", "user", "cpu", "mem", "gpu", "gpu_mem")
        data = self.ctx.data or {}
        game = (data.get("game") or {})
        mem = data.get("memory") or {}
        gpus = [{k: g.get(k) for k in ("name", "busy", "temp", "vram", "gtt", "power", "clocks")}
                for g in (data.get("gpu") or {}).get("gpus", [])]
        return {"captured": time.time(),
                "top_cpu": [{k: p.get(k) for k in keep} for p in top_cpu],
                "top_mem": [{k: p.get(k) for k in keep} for p in top_mem],
                "game": {"name": (game.get("game") or {}).get("name"), "fps": game.get("fps"), "low1": game.get("low1")}
                if game.get("game") else None,
                "memory": {k: mem.get(k) for k in ("total", "used", "available", "percent", "swap_total", "swap_used")
                           if k in mem}, "gpus": gpus,
                "uptime": time.time() - _boot_time()}

    # ---------- laço ----------
    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="investigator").start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        self._stop.wait(8)
        try:
            self.check_previous_boot()
        except Exception:  # noqa: BLE001
            log.exception("verificação do boot anterior falhou")
        while not self._stop.is_set():
            now = time.time()
            with self.lock:
                due = [(i, p) for i, p in self.pending.items() if p["due"] <= now]
                for i, _ in due:
                    self.pending.pop(i, None)
            for iid, p in due:
                try:
                    self.build(iid, p)
                except Exception:  # noqa: BLE001
                    log.exception("relatório do incidente %s falhou", iid)
                    self.store.update_incident(iid, status="open", summary="Falha ao montar o relatório; veja o log do painel.")
            self._stop.wait(2)

    # ---------- investigação manual ----------
    def investigate(self, at: float, before: float = PRE, after: float = POST, note: str | None = None) -> int:
        now = time.time()
        at = min(float(at), now)
        inc = {"uid": uuid.uuid4().hex[:12], "opened": now, "trigger_ts": at, "family": "manual", "kind": "manual",
               "severity": "info", "status": "analyzing", "title": f"Investigação manual de {_when(at)}", "subject": None}
        iid = self.store.add_incident(inc)
        p = {"first": at, "last": at, "events": [], "family": "manual", "kind": "manual", "severity": "info",
             "live": self._live_context() if now - at < 120 else None, "pre": max(30, min(before, 6 * 3600)),
             "post": max(0, min(after, 3600)), "note": note}
        threading.Thread(target=self._safe_build, args=(iid, p), daemon=True).start()
        return iid

    def _safe_build(self, iid: int, p: dict) -> None:
        try:
            self.build(iid, p)
        except Exception:  # noqa: BLE001
            log.exception("investigação manual falhou")
            self.store.update_incident(iid, status="open", summary="Falha ao montar o relatório.")

    # ---------- boot anterior ----------
    def check_previous_boot(self) -> int | None:
        """O Windows grava, na partida seguinte, Kernel-Power 41 e/ou EventLog 6008 quando o desligamento anterior
        não foi normal (falta de energia, travamento total, tela azul, botão segurado)."""
        boots = eventlog.boots(limit=3)
        cur = next((b for b in boots if b["index"] == 0), None)
        prev = next((b for b in boots if b["index"] == -1), None)
        if not cur or not prev or self.store.get_meta("boot_checked") == prev["id"]:
            return None
        self.store.set_meta("boot_checked", prev["id"])
        xp = eventlog._xpath(["(Provider[@Name='Microsoft-Windows-Kernel-Power'] and EventID=41) or "
                              "(Provider[@Name='EventLog'] and EventID=6008)",
                              eventlog._time_cond(cur["first"] - 60, cur["first"] + 900)])
        marks = eventlog._read("System", xp, 5, reverse=False)
        if not marks:
            return None
        bugcheck = next((m["data"].get("BugcheckCode") for m in marks if m["event_id"] == 41
                         and m["data"].get("BugcheckCode") not in (None, "", "0")), None)
        msgs = eventlog.around(prev["first"], cur["first"] - 1, limit=300)
        last = msgs[-1]["ts"] if msgs else prev["last"]
        for m in msgs:
            m.pop("data", None)
        detail = f" (tela azul, código {int(bugcheck):#x})" if bugcheck and bugcheck.isdigit() else ""
        ev = {"ts": last, "kind": "unexpected_shutdown", "family": "power", "severity": "critical", "trigger": True,
              "source": "boot", "unit": None, "ident": None, "pid": None, "boot": prev["id"],
              "message": f"O Windows anterior terminou sem desligamento normal{detail} (último registro às {_hm(last)}).",
              "data": {"label": "Desligamento inesperado", "boot": prev["id"], "bugcheck": bugcheck}}
        eid = self.store.add_event(ev)
        inc = {"uid": uuid.uuid4().hex[:12], "opened": time.time(), "trigger_ts": last, "family": "power",
               "kind": "unexpected_shutdown", "severity": "critical", "status": "analyzing",
               "title": TITLES["unexpected_shutdown"], "subject": "power:unexpected_shutdown"}
        iid = self.store.add_incident(inc)
        self.store.link_events([eid], iid)
        self.build(iid, {"first": last, "last": last, "events": [(ev, eid)], "family": "power",
                         "kind": "unexpected_shutdown", "severity": "critical", "live": None, "boot": prev["id"],
                         "tail": msgs[-60:], "bugcheck": bugcheck})
        if self.ctx.alerts:
            self.ctx.alerts.event("incident", f"O PC desligou sem desligamento normal às {_when(last)}.", "critical",
                                  key=inc["uid"])
        return iid

    # ---------- relatório ----------
    def build(self, iid: int, p: dict) -> dict:
        evs = [e for e, _ in p["events"]]
        t = p["first"]
        t0, t1 = t - p.get("pre", PRE), p["last"] + p.get("post", POST)
        stored = self.store.events(since=t0, until=t1, limit=600)
        raw = eventlog.window(t0, t1, limit=500)
        if p.get("tail"):
            raw = [m for m in p["tail"] if m["ts"] >= t0] or p["tail"]
        main = min(evs, key=lambda e: (_rank(e["kind"]), e["ts"])) if evs else None
        subject = self._subject(evs)
        metrics = self._metrics(t0, t1, t)
        changes = self._changes(t0, t1, t)
        recurrence = self._recurrence(p, subject)
        facts = self._facts(evs, stored, metrics, changes, subject, p)
        hyps = hypotheses(facts)
        timeline = self._timeline(stored, changes, evs, t0, t1)
        title = self._title(p["kind"], evs, subject)
        summary = (hyps[0]["title"] + f" (confiança {hyps[0]['level']})") if hyps else "Sem hipótese forte; veja a linha do tempo."
        report = {
            "version": 1, "window": [t0, t1], "trigger_ts": t, "main": main, "triggers": evs, "subject": subject,
            "timeline": timeline, "metrics": metrics, "context": p.get("live"), "changes": changes,
            "logs": _collapse(raw)[-200:],
            "hypotheses": hyps, "recurrence": recurrence, "system": self._system(), "note": p.get("note"),
            "built": time.time(),
        }
        report["ai_text"] = ai_text(title, report)
        self.store.update_incident(iid, report=report, status="open", title=title, summary=summary,
                                   kind=p["kind"], family=p["family"], severity=p["severity"],
                                   subject=self._subject_key(main) if main else None)
        log.info("incidente %s: %s — %s", iid, title, summary)
        return report

    def _title(self, kind: str, evs: list[dict], subject: dict | None = None) -> str:
        crash = next((e for e in evs if e["kind"] == "coredump"), None)
        name = None
        if subject and subject.get("name"):
            name = subject.get("game") or subject["name"]
        elif crash:
            name = _proc_name(crash.get("data") or {})
        gpu = any(e["family"] == "gpu" for e in evs)
        if kind == "coredump" or (kind in ("gpu_hang", "gpu_reset", "gpu_fault") and crash):
            sig = (crash.get("data") or {}).get("signal") if crash else None
            if gpu:
                return f"GPU travou e {name or 'um programa'} caiu"
            if subject and subject.get("launcher") and subject.get("game"):
                return f"{subject['game']} travou ({sig or '?'}, aberto pelo {subject['launcher']})"
            return f"{name or 'Programa'} travou" + (f" ({sig})" if sig else "")
        if kind == "unit_failed":
            unit = next((e.get("unit") for e in evs if e["kind"] == "unit_failed"), None)
            disp = next(((e.get("data") or {}).get("display") for e in evs if e["kind"] == "unit_failed"), None)
            return f"Serviço {disp or unit or '?'} falhou"
        return TITLES.get(kind, "Incidente")

    def _subject(self, evs: list[dict]) -> dict | None:
        crash = next((e for e in evs if e["kind"] == "coredump"), None)
        if crash:
            d = crash.get("data") or {}
            exe = d.get("exe") or ""
            s = {"type": "process", "name": _proc_name(d), "exe": exe, "pid": crash.get("pid"),
                 "signal": d.get("signal"), "meaning": d.get("meaning"), "module": d.get("module"),
                 "module_version": d.get("module_version"), "app_version": d.get("app_version"),
                 "stack": _stack(crash), "core": _dump_for(d, crash["ts"])}
            s.update(_process_extra(self.ctx, crash))
            return s
        unitf = next((e for e in evs if e["kind"] == "unit_failed"), None)
        if unitf:
            from . import services
            unit = unitf.get("unit")
            disp = (unitf.get("data") or {}).get("display")
            info = {}
            if unit and services.valid_unit(unit):
                try:
                    info = services.detail(unit).get("props") or {}
                except (ValueError, OSError):
                    info = {}
            return {"type": "unit", "name": disp or unit, "unit": unit, "result": (unitf.get("data") or {}).get("result"),
                    "unit_info": info, "logs": eventlog.service_logs(unit or "", disp, 40) if unit else []}
        if any(e["family"] == "gpu" for e in evs):
            gpus = (self.ctx.data.get("gpu") or {}).get("gpus") or []
            dgpu = next((g for g in gpus if not g.get("integrated")), gpus[0] if gpus else {})
            hang = next((e for e in evs if (e.get("data") or {}).get("dump")), None)
            return {"type": "gpu", "name": dgpu.get("name") or "GPU", "core": (hang or {}).get("data", {}).get("dump")}
        return None

    def _metrics(self, t0: float, t1: float, t: float) -> dict:
        live = self.ctx.history.get(None, since=t0)
        series: dict[str, list] = {}
        source = "ao vivo (1 s)"
        if live and any(v and v[0][0] <= t for v in live.values()):
            for k, pts in live.items():
                if k in METRICS:
                    series[k] = [[ts, v] for ts, v in pts if ts <= t1]
        if not series:
            source = "resumo por minuto"
            for k, pts in self.store.metrics(list(METRICS), t0 - 120, t1 + 60, res=60).items():
                series[k] = [[ts + 30, avg] for ts, avg, _mn, _mx in pts]
                series[k + ":max"] = [[ts + 30, mx] for ts, _a, _mn, mx in pts]
        summary = []
        for k, (label, unit) in METRICS.items():
            pts = series.get(k)
            if not pts:
                continue
            maxs = series.get(k + ":max") or pts
            before = [v for ts, v in pts if ts < t - 30]
            near = [v for ts, v in pts if t - 15 <= ts <= t + 5] or [v for ts, v in pts if abs(ts - t) <= 90]
            summary.append({"key": k, "label": label, "unit": unit,
                            "before": round(sum(before) / len(before), 1) if before else None,
                            "at": round(max(near), 1) if near else None,
                            "max": round(max(v for _ts, v in maxs), 1), "min": round(min(v for _ts, v in pts), 1)})
        compact = {k: _downsample(v, 120) for k, v in series.items() if not k.endswith(":max")}
        return {"source": source if summary else None, "summary": summary, "series": compact}

    def _changes(self, t0: float, t1: float, t: float) -> dict:
        pkgs = [u for u in hwinfo.recent_upgrades(days=3, now=t) if RELEVANT_PKGS.match(u["package"]) and u["ts"] <= t]
        hw = [h for h in self.store.hardware_history(limit=20) if h.get("changes") and t - 7 * 86400 <= h["ts"] <= t]
        panel = []
        if self.audit:
            for a in self.audit.list(limit=300):
                if t0 <= a["time"] <= t1:
                    panel.append({"ts": a["time"], "text": a["text"], "cat": a["cat"], "device": a.get("device")})
        files = []
        for f in OVERLAY_FILES:
            path = os.path.expandvars(f)
            try:
                m = os.stat(path).st_mtime
            except OSError:
                continue
            if t0 <= m <= t1 or (t - 600 <= m <= t):
                files.append({"ts": m, "path": path})
        return {"packages": pkgs[-40:], "hardware": hw, "panel": panel, "files": files}

    def _recurrence(self, p: dict, subject: dict | None) -> dict:
        key = None
        main = min((e for e, _ in p["events"]), key=lambda e: _rank(e["kind"]), default=None)
        if main:
            key = self._subject_key(main)
        if not key:
            return {}
        week = [i for i in self.store.incidents(limit=200) if i["subject"] == key and i["trigger_ts"] >= p["first"] - 7 * 86400]
        return {"key": key, "last_7d": len(week), "total_repeats": sum(i["count"] for i in week)}

    def _facts(self, evs, stored, metrics, changes, subject, p) -> dict:
        t = p["first"]
        kinds = Counter(e["kind"] for e in stored)
        near_gpu = [e for e in stored if e["family"] == "gpu" and e["kind"] in ("gpu_hang", "gpu_reset", "gpu_fault")
                    and t - 120 <= e["ts"] <= t + 15 and not (e.get("data") or {}).get("old_report")]
        msum = {m["key"]: m for m in metrics.get("summary", [])}
        crash = next((e for e in evs if e["kind"] == "coredump"), None)
        cdata = (crash or {}).get("data") or {}
        exe = (cdata.get("exe") or "") + " " + (cdata.get("cmdline") or "")
        unitf = next((e for e in evs if e["kind"] == "unit_failed"), None)
        hw = self.store.hardware_latest()
        return {
            "t": t, "kind": p["kind"], "family": p["family"], "kinds": kinds, "near_gpu": near_gpu,
            "gpu_fence": [e for e in stored if e["kind"] == "gpu_fence" and t - 300 <= e["ts"] <= t + 15],
            "crash": crash, "signal": cdata.get("signal"),
            "is_game": bool(re.search(r"steamapps|\\Games\\|Epic Games|GOG Galaxy|XboxGames", exe, re.I)),
            "mod_launcher": bool(re.search(r"launcher|ersc|seamless|modengine|dinput8|reshade|\bmods?\b",
                                           exe + " " + str((subject or {}).get("launcher") or ""), re.I)),
            "module": cdata.get("module"), "code": cdata.get("code"),
            "bugcheck": [e for e in stored if e["kind"] == "bugcheck"] + ([{"ts": t, "message": f"código {p['bugcheck']}"}]
                                                                         if p.get("bugcheck") else []),
            "temps": {k: msum[k] for k in ("cpu.temp", "gpu.0.temp") if k in msum},
            "mem": msum.get("memory.percent"), "gtt": msum.get("gpu.0.gtt"), "vram": msum.get("gpu.0.vram"),
            "swap_total": ((hw or {}).get("data") or {}).get("memory", {}).get("swap") if hw else None,
            "oom": [e for e in stored if e["kind"] == "oom"],
            "storage": [e for e in stored if e["family"] == "storage"],
            "mce": [e for e in stored if e["kind"] == "mce"], "aer": [e for e in stored if e["kind"] in ("pcie_aer", "mce_corrected")],
            "kernel_bug": [e for e in stored if e["kind"] == "kernel_bug"],
            "thermal": [e for e in stored if e["kind"] == "thermal" and e["severity"] == "critical"],
            "unit": unitf, "unit_result": ((unitf or {}).get("data") or {}).get("result"), "subject": subject,
            "files": [f for f in changes["files"] if f["ts"] <= t + 2],
            "panel": [a for a in changes["panel"] if a["ts"] <= t + 2],
            "packages": changes["packages"], "hardware": changes["hardware"],
            "metrics_available": bool(metrics.get("summary")), "tail": p.get("tail"),
        }

    def _timeline(self, stored, changes, evs, t0, t1) -> list[dict]:
        out = []
        for e in stored:
            label = (e.get("data") or {}).get("label") if isinstance(e.get("data"), dict) else None
            out.append({"ts": e["ts"], "kind": e["kind"], "family": e["family"], "severity": e["severity"],
                        "source": e["source"], "who": e.get("ident") or e.get("unit"), "text": e["message"][:400],
                        "label": label, "count": e.get("count", 1), "trigger": e["kind"] in {x["kind"] for x in evs}})
        for a in changes["panel"]:
            out.append({"ts": a["ts"], "kind": "panel_action", "family": "change", "severity": "info", "source": "painel",
                        "who": a.get("device"), "text": a["text"], "label": "Ação feita pelo painel"})
        for f in changes["files"]:
            if t0 <= f["ts"] <= t1:
                out.append({"ts": f["ts"], "kind": "file_changed", "family": "change", "severity": "notice", "source": "arquivo",
                            "who": None, "text": f"{f['path']} foi alterado", "label": "Configuração de overlay alterada"})
        for u in changes["packages"]:
            if t0 <= u["ts"] <= t1:
                out.append({"ts": u["ts"], "kind": "package", "family": "change", "severity": "notice", "source": "Windows Update",
                            "who": None, "text": f"{u['package']} ({u['action']})", "label": "Atualização"})
        out.sort(key=lambda x: x["ts"])
        return out[-300:]

    def _system(self) -> dict:
        hw = self.store.hardware_latest()
        d = (hw or {}).get("data") or {}
        return {**fleet.identity(), "board": d.get("board"), "cpu": (d.get("cpu") or {}).get("model"),
                "gpus": [g.get("name") or g.get("device_id") for g in d.get("gpus") or []],
                "memory": (d.get("memory") or {}).get("total"), "packages": d.get("packages"), "os_name": d.get("os")}


# ---------- hipóteses ----------
def _h(hid: str, title: str, score: int, evidence: list[str], checks: list[str]) -> dict:
    score = max(0, min(100, score))
    return {"id": hid, "title": title, "score": score, "level": "alta" if score >= 75 else "média" if score >= 50 else "baixa",
            "evidence": [e for e in evidence if e], "checks": checks}


def hypotheses(f: dict) -> list[dict]:
    """Regras de correlação -> hipóteses ordenadas por confiança."""
    out: list[dict] = []
    t = f["t"]
    crash = f["crash"]

    def rel(ts: float) -> str:
        d = ts - t
        return f"{abs(d):.0f} s {'antes' if d < 0 else 'depois'}" if abs(d) >= 1 else "no mesmo segundo"

    if f["bugcheck"]:
        b = f["bugcheck"]
        out.append(_h("bsod", "Tela azul (o Windows parou por um erro grave de driver ou de hardware)", 90,
                      [f"{_hm(e['ts'])}: {e['message'][:200]}" for e in b[:3]], [
                          "Código do erro (bugcheck) e o arquivo .dmp citado: abra no WinDbg ou no BlueScreenView",
                          "Driver atualizado há pouco (seção de mudanças); voltar a versão anterior",
                          "Overclock/undervolt de CPU, RAM (EXPO/XMP) ou GPU; teste de memória (Diagnóstico de Memória do Windows)",
                      ]))

    if f["near_gpu"]:
        ev = f["near_gpu"]
        kinds = {e["kind"] for e in ev}
        evidence = [f"{_hm(e['ts'])} ({rel(e['ts'])}): {e['message'][:160]}" for e in ev[:5]]
        score = 88 if kinds & {"gpu_hang", "gpu_reset"} else 70
        if crash:
            evidence.append(f"{crash['data'].get('comm') or 'o programa'} caiu {rel(crash['ts']).replace('no mesmo segundo', 'junto')} do erro da GPU")
        title = ("Falha da GPU ou do driver de vídeo: o Windows detectou o travamento e reiniciou o driver (TDR)"
                 if kinds & {"gpu_hang", "gpu_reset"} else "Acesso inválido à memória da GPU (bug do driver, do jogo ou dos shaders)")
        out.append(_h("gpu", title, score, evidence, [
            "Temperatura, clock e consumo da GPU no momento (tabela de métricas)",
            "Overclock/undervolt da GPU (MSI Afterburner, NVIDIA App): voltar ao padrão e testar",
            "Driver de vídeo atualizado há pouco (seção de mudanças): testar a versão anterior (DDU + instalação limpa)",
            "Overlays e captura (Discord, Steam, NVIDIA App, RTSS) e a versão do jogo/DirectX",
        ]))

    hot = [(k, m) for k, m in f["temps"].items() if (m.get("max") or 0) >= 90]
    if f["thermal"] or hot:
        evidence = [f"{m['label']}: pico de {m['max']}{m['unit']} (antes: {m['before']}{m['unit']})" for _k, m in hot]
        evidence += [f"{_hm(e['ts'])}: {e['message'][:140]}" for e in f["thermal"][:3]]
        out.append(_h("thermal", "Superaquecimento / throttling", 80 if f["thermal"] else 60, evidence, [
            "Poeira, pasta térmica e fluxo de ar; curva das ventoinhas (software da placa-mãe ou FanControl)",
            "Temperatura ambiente; limite de potência",
        ]))
    elif f["temps"]:
        pass

    mem = f["mem"]
    if f["oom"] or (mem and (mem.get("max") or 0) >= 95):
        evidence = [f"{_hm(e['ts'])}: {e['message'][:160]}" for e in f["oom"][:3]]
        if mem:
            evidence.append(f"Memória: pico de {mem['max']}% (antes: {mem['before']}%)")
        if f["swap_total"] == 0:
            evidence.append("O Windows está sem arquivo de paginação: quando a RAM acaba, programas caem na hora")
        out.append(_h("memory", "Falta de memória", 92 if f["oom"] else 62, evidence, [
            "Processos que mais usavam memória no momento (contexto)",
            "Arquivo de paginação gerenciado pelo sistema; fechar navegador/apps pesados durante o jogo",
            "Vazamento: o uso sobe até o programa cair (veja a métrica de memória antes)",
        ]))

    elif mem and (mem.get("max") or 0) >= 88 and f["swap_total"] == 0:
        out.append(_h("memory_low", "Pouca folga de memória e sem arquivo de paginação", 40, [
            f"Memória: pico de {mem['max']}% e o Windows está sem arquivo de paginação"], [
            "Qualquer pico (navegador, cache de shaders, jogo carregando) pode derrubar um programa",
            "Voltar o arquivo de paginação para 'gerenciado pelo sistema'",
        ]))

    gtt = f["gtt"]
    if crash and gtt and (gtt.get("max") or 0) >= 95:
        out.append(_h("gpu_mem", "Memória compartilhada da GPU no limite", 55, [f"Compartilhada: pico de {gtt['max']}%"],
                      ["Reduzir texturas/resolução; ver se há vazamento (uso subindo até cair)"]))

    vram = f["vram"]
    if crash and vram and (vram.get("max") or 0) >= 97:
        out.append(_h("vram", "VRAM da GPU no limite", 55, [f"VRAM: pico de {vram['max']}%"],
                      ["Reduzir texturas/resolução/ray tracing; fechar outros programas que usam a GPU"]))

    if crash:
        recent_files = [x for x in f["files"] if 0 <= t - x["ts"] <= 300]
        panel = [a for a in f["panel"] if 0 <= t - a["ts"] <= 300 and re.search(r"FPS|PresentMon|resolução|tela", a["text"], re.I)]
        if recent_files or panel:
            near = min([t - x["ts"] for x in recent_files] + [t - a["ts"] for a in panel])
            ev = [f"{_hm(x['ts'])} ({rel(x['ts'])}): {x['path']} alterado" for x in recent_files]
            ev += [f"{_hm(a['ts'])} ({rel(a['ts'])}): pelo painel — {a['text'][:120]}" for a in panel[:4]]
            out.append(_h("overlay", "Mudança de overlay ou de tela com o jogo aberto", 62 if near <= 60 else 42, ev, [
                "RivaTuner/Afterburner aplicam o perfil dentro do jogo; alguns jogos caem na troca",
                "Trocar resolução/taxa com o jogo em tela cheia exclusiva pode derrubar o jogo",
                "Fazer a mudança com o jogo fechado e testar de novo",
            ]))
        if f["mod_launcher"]:
            launcher = (f["subject"] or {}).get("launcher")
            out.append(_h("mod", "Mod ou launcher de terceiros", 55, [
                f"Aberto pelo atalho \"{f['subject'].get('shortcut')}\" → {launcher}" if launcher else
                f"Executado por: {(crash['data'].get('cmdline') or crash['data'].get('exe') or '')[:200]}"], [
                "Versão do mod compatível com a versão do jogo (atualizações do jogo quebram mods)",
                "Testar o jogo sem o mod; ver o log do próprio mod",
            ]))
        sig = f["signal"] or "?"
        others = [h for h in out if h["score"] >= 60]
        base = 58 if not others else 30
        ev = [f"Exceção {sig}: {crash['data'].get('meaning') or 'veja o código'}"]
        if f["module"]:
            mod = f["module"]
            ev.append(f"Módulo com falha: {mod}" + (
                " (biblioteca do Windows: a falha costuma ser do programa que a chamou)" if re.match(r"(ntdll|kernelbase|ucrtbase|msvcp|vcruntime)", mod, re.I)
                else " (driver de vídeo)" if re.match(r"(nvwgf|nvd3d|nvoglv|nvlddmkm|amdxx|atiu|igd|igxel|d3d1[12])", mod, re.I)
                else ""))
            if re.match(r"(nvwgf|nvd3d|nvoglv|amdxx|atiu|igd|igxel)", mod, re.I):
                base = max(base, 60)
        if f["is_game"]:
            ev.append("Jogo instalado por uma loja (Steam/Epic/GOG/Xbox)")
        if f["subject"] and f["subject"].get("runtime"):
            ev.append(f"Rodou por {_dur(f['subject']['runtime'])} antes de cair")
        if not f["near_gpu"] and not hot and not f["oom"]:
            ev.append("Nenhum erro de GPU, temperatura ou memória perto da queda")
        out.append(_h("app", "Falha do próprio programa (bug, arquivos do jogo ou bibliotecas)", base, ev, [
            "Verificar a integridade dos arquivos (Steam/Epic/GOG)",
            "Reinstalar o Visual C++ Redistributable e o DirectX; atualizar o jogo",
            "Desligar overlays (Discord, Steam, NVIDIA App, RTSS) e mods para testar",
            "Ver se cai sempre no mesmo ponto (repetições abaixo)",
        ]))

    if f["storage"]:
        out.append(_h("storage", "Problema no disco ou no sistema de arquivos", 85,
                      [f"{_hm(e['ts'])}: {e['message'][:160]}" for e in f["storage"][:4]],
                      ["Saúde do disco (SMART: CrystalDiskInfo)", "Cabo/slot do disco; espaço livre", "chkdsk /f no volume citado"]))
    if f["mce"]:
        out.append(_h("mce", "Erro de hardware da CPU, da memória ou do PCIe (WHEA)", 85,
                      [f"{_hm(e['ts'])}: {e['message'][:160]}" for e in f["mce"][:3]],
                      ["Overclock/undervolt de CPU ou RAM (EXPO/XMP, Curve Optimizer)", "Teste de memória (MemTest86)",
                       "BIOS atualizado"]))
    if f["aer"]:
        out.append(_h("pcie", "Erros de hardware corrigidos (WHEA)", 45, [f"{_hm(e['ts'])}: {e['message'][:160]}" for e in f["aer"][:3]],
                      ["Placa bem encaixada; riser/cabos", "Atualização do BIOS"]))
    if f["kernel_bug"]:
        out.append(_h("kernel", "Falha no kernel (driver)", 78, [f"{_hm(e['ts'])}: {e['message'][:160]}" for e in f["kernel_bug"][:3]],
                      ["Kernel atualizado há pouco? Testar o kernel anterior/LTS", "Módulo citado no rastreio"]))

    if f["kind"] == "unexpected_shutdown":
        tail = f.get("tail") or []
        errs = [m for m in tail[-40:] if m["prio"] <= 3]
        gpu_tail = [m for m in tail[-60:] if re.search(r"nvlddmkm|amdkmdag|Display|LiveKernelEvent", f"{m['ident']} {m['msg']}", re.I)
                    and m["prio"] <= 3]
        if f["bugcheck"]:
            pass                              # a tela azul já explica
        elif gpu_tail:
            out.append(_h("shutdown_gpu", "Travamento total causado pela GPU", 70, [f"{_hm(m['ts'])}: {m['msg'][:150]}" for m in gpu_tail[-3:]],
                          ["Mesmos pontos de 'GPU travou'; ver temperatura nas métricas por minuto"]))
        hot_tail = [m for m in f["temps"].values() if (m.get("max") or 0) >= 90]
        if hot_tail:
            out.append(_h("shutdown_thermal", "Desligamento por temperatura", 65,
                          [f"{m['label']}: {m['max']}{m['unit']} no último minuto registrado" for m in hot_tail], ["Ventilação"]))
        out.append(_h("shutdown_power", "Queda de energia, botão de reset ou travamento sem registro",
                      60 if not errs and not gpu_tail and not f["bugcheck"] else 35,
                      ["O log de eventos parou sem as mensagens de desligamento (Kernel-Power 41)"]
                      + [f"Últimos erros: {m['msg'][:120]}" for m in errs[-2:]],
                      ["Houve queda de luz? Fonte/estabilizador", "Travou com a tela congelada antes?",
                       "Inicialização rápida/hibernação: desligar o PC pelo botão durante a hibernação também gera este aviso"]))

    if f["unit"]:
        res = f["unit_result"] or "?"
        info = (f["subject"] or {}).get("unit_info") or {}
        ev = [f"Resultado: {res}"]
        code = info.get("ServiceSpecificExitCode") if info.get("ExitCode") == 1066 else info.get("ExitCode")
        if code:
            ev.append(f"Código de saída: {code}")
        if info.get("StartMode"):
            ev.append(f"Modo de início: {info['StartMode']}")
        title, score = {
            "crash": ("O processo do serviço terminou inesperadamente (travou ou foi encerrado)", 65),
            "exit-code": ("O serviço terminou com erro (configuração, dependência ou arquivo ausente)", 60),
            "start-failed": ("O serviço não conseguiu iniciar", 55),
            "timeout": ("O serviço demorou demais para iniciar/responder", 60),
            "dependency": ("Um serviço do qual ele depende não iniciou", 60),
            "logon": ("A conta do serviço não conseguiu entrar (senha/permissão)", 65),
        }.get(res, ("O serviço falhou", 50))
        out.append(_h("unit", title, score, ev, ["Eventos do serviço (abaixo)", "Aba Serviços: estado e dependências",
                                                 "Reinstalar/atualizar o programa dono do serviço"]))

    if f["packages"] and (crash or f["near_gpu"] or f["kind"] in ("kernel_bug", "bugcheck", "unexpected_shutdown")):
        names = sorted({u["package"] for u in f["packages"]})
        newest = max(u["ts"] for u in f["packages"])
        out.append(_h("update", "Atualização recente do Windows ou de driver", 35 if t - newest > 86400 else 45,
                      [f"Instaladas nos 3 dias antes: {'; '.join(names[:8])}", f"Mais recente: {_when(newest)}"],
                      ["Se o problema começou depois da atualização: Configurações > Windows Update > Histórico > Desinstalar",
                       "Driver de vídeo: voltar a versão anterior pelo Gerenciador de Dispositivos ou pelo site do fabricante"]))
    if f["hardware"]:
        out.append(_h("hwchange", "Mudança de hardware/BIOS/driver na última semana", 35,
                      [", ".join(c["path"] for c in (h.get("changes") or [])[:6]) for h in f["hardware"][:3]],
                      ["Comparar com o histórico de hardware (troca de peça, BIOS ou versão do driver)"]))
    if not f["metrics_available"] and f["kind"] != "manual":
        for h in out:
            h["evidence"].append("(sem métricas daquele momento: o painel não estava gravando)")
            break
    out.sort(key=lambda h: h["score"], reverse=True)
    return out


# ---------- texto para IA ----------
def ai_text(title: str, r: dict) -> str:
    s = r.get("system") or {}
    lines = [f"# Incidente: {title}", "",
             f"- Quando: {_when(r['trigger_ts'])} (janela analisada: {_hm(r['window'][0])} a {_hm(r['window'][1])})",
             f"- Máquina: {s.get('hostname')} · {s.get('os_name') or 'Windows'} · build {s.get('kernel')}",
             f"- Placa: {((s.get('board') or {}).get('vendor') or '')} {((s.get('board') or {}).get('model') or '')}"
             f" (BIOS {((s.get('board') or {}).get('bios_version') or '?')}) · CPU {s.get('cpu')} · GPU {', '.join(filter(None, s.get('gpus') or []))}",
             ]
    pk = s.get("packages") or {}
    if pk:
        lines.append("- Versões: " + ", ".join(f"{k} {v}" for k, v in list(pk.items())[:10]))
    sub = r.get("subject") or {}
    if sub.get("type") == "process":
        lines += ["", "## Programa envolvido",
                  f"- {sub.get('game') or sub.get('name')} {sub.get('app_version') or ''} — executável `{sub.get('exe')}` (PID {sub.get('pid')}),"
                  f" exceção {sub.get('signal')} ({sub.get('meaning') or '?'}), módulo com falha {sub.get('module')} {sub.get('module_version') or ''}"]
        if sub.get("cmdline"):
            lines.append(f"- Linha de comando: `{sub['cmdline'][:400]}`")
        if sub.get("runtime"):
            lines.append(f"- Tempo rodando antes de cair: {_dur(sub['runtime'])}")
        if sub.get("stack"):
            lines += ["- Detalhes da falha:", "```", *sub["stack"][:25], "```"]
    elif sub.get("type") == "unit":
        lines += ["", "## Serviço envolvido", f"- {sub.get('name')} — resultado: {sub.get('result')}"]
        if sub.get("logs"):
            lines += ["```", *[f"{_hm(m['ts'])} {m['msg'][:200]}" for m in sub["logs"][-25:]], "```"]
    if r.get("repeats"):
        lines.append(f"- Aconteceu de novo {len(r['repeats'])} vez(es) em seguida")
    rec = r.get("recurrence") or {}
    if rec.get("last_7d", 0) > 1:
        lines.append(f"- Incidentes iguais nos últimos 7 dias: {rec['last_7d']}")
    lines += ["", "## Linha do tempo"]
    for e in r.get("timeline", [])[-60:]:
        mark = " **[gatilho]**" if e.get("trigger") else ""
        cnt = f" (x{e['count']})" if e.get("count", 1) > 1 else ""
        lines.append(f"- {_hm(e['ts'])} [{e['source']}{'/' + e['who'] if e.get('who') else ''}] {e['text'][:220]}{cnt}{mark}")
    m = r.get("metrics") or {}
    if m.get("summary"):
        lines += ["", f"## Métricas ({m.get('source')})", "| Métrica | Antes | No momento | Pico | Mínimo |", "|---|---|---|---|---|"]
        for x in m["summary"]:
            u = x["unit"]
            lines.append(f"| {x['label']} | {_fmt(x['before'], u)} | {_fmt(x['at'], u)} | {_fmt(x['max'], u)} | {_fmt(x['min'], u)} |")
    c = r.get("context") or {}
    if c:
        lines += ["", "## Processos no momento (mais CPU)"]
        for p in c.get("top_cpu", [])[:6]:
            lines.append(f"- {p.get('name')} (PID {p.get('pid')}): CPU {p.get('cpu')}%, memória {_bytes(p.get('mem'))}")
        if c.get("game"):
            lines.append(f"- Jogo aberto: {c['game'].get('name')} · FPS {c['game'].get('fps')}")
    ch = r.get("changes") or {}
    if ch.get("packages"):
        lines += ["", "## Atualizações instaladas nos 3 dias antes"]
        lines += [f"- {_when(u['ts'])}: {u['package']}" for u in ch["packages"][-15:]]
    lines += ["", "## Hipóteses do painel (regras, sem IA)"]
    for h in r.get("hypotheses", []):
        lines.append(f"- **{h['title']}** — confiança {h['level']} ({h['score']}/100)")
        lines += [f"  - evidência: {e}" for e in h["evidence"][:5]]
    if r.get("logs"):
        lines += ["", "## Trecho do log de eventos do Windows (avisos e erros, Sistema e Aplicativo)", "```", *r["logs"][-80:], "```"]
    if r.get("note"):
        lines += ["", f"Observação do usuário: {r['note']}"]
    lines += ["", "## Pergunta",
              "Com base nesses dados, qual a causa mais provável? As hipóteses acima fazem sentido? "
              "O que devo verificar ou testar, em ordem, para confirmar e resolver?"]
    return "\n".join(lines)


# ---------- auxiliares ----------
def _proc_name(d: dict) -> str:
    return d.get("comm") or os.path.basename(d.get("exe") or "") or "?"


def _collapse(raw: list[dict]) -> list[str]:
    """Linhas de log; repetições seguidas viram uma com (xN)."""
    out: list[str] = []
    prev, n = None, 0
    for m in raw:
        if NOISE.search(m["msg"] or ""):
            continue
        key = (m["ident"], m["msg"])
        if key == prev:
            n += 1
            continue
        if prev is not None and n > 1:
            out[-1] += f" (x{n})"
        prev, n = key, 1
        first = m["msg"].splitlines()[0] if m["msg"] else ""
        out.append(f"{_hm(m['ts'])} {m['ident']}: {first}"[:300])
    if n > 1 and out:
        out[-1] += f" (x{n})"
    return out


def _fmt(v, unit: str) -> str:
    if v is None:
        return "—"
    if unit == "B/s":
        return _bytes(v) + "/s"
    return f"{v}{(' ' + unit) if unit and unit not in ('%', '°C') else unit}"


def _bytes(n) -> str:
    if n is None:
        return "?"
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"


def _dur(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60} s"
    return f"{s // 3600} h {(s % 3600) // 60} min"


def _downsample(pts: list, n: int) -> list:
    if len(pts) <= n:
        return pts
    step = len(pts) / n
    return [pts[int(i * step)] for i in range(n)] + [pts[-1]]


def _stack(crash: dict) -> list[str]:
    """O Windows não grava a pilha no log; o que há: exceção, módulo, versão e posição da falha."""
    d = crash.get("data") or {}
    out = []
    if d.get("signal"):
        out.append(f"Exceção: {d['signal']}" + (f" ({d['meaning']})" if d.get("meaning") else ""))
    if d.get("module"):
        out.append(f"Módulo: {d['module']} {d.get('module_version') or ''}".strip())
    if d.get("module_path"):
        out.append(f"Caminho do módulo: {d['module_path']}")
    if d.get("offset"):
        out.append(f"Posição no módulo: 0x{d['offset'].lstrip('0') or '0'}")
    if d.get("report"):
        out.append(f"Relatório do Windows (WER): {d['report']}")
    return out


def _dump_for(d: dict, ts: float) -> str | None:
    """Minidump do programa, se o Windows estiver configurado para gravar (LocalDumps): %LOCALAPPDATA%\\CrashDumps."""
    name = (d.get("comm") or "").lower()
    if not name:
        return None
    for folder in (Path(os.path.expandvars(r"%LOCALAPPDATA%\CrashDumps")), _home() / "AppData" / "Local" / "CrashDumps"):
        try:
            for f in folder.glob("*.dmp"):
                if f.name.lower().startswith(name) and abs(f.stat().st_mtime - ts) < 120:
                    return str(f)
        except OSError:
            continue
    return None


def _process_extra(ctx, crash: dict) -> dict:
    """Jogo (Steam), tempo rodando (rastreador de processos)."""
    out: dict = {}
    d = crash.get("data") or {}
    from . import game
    appid = game._common_appid(d.get("exe") or "")
    if appid:
        out["game"] = game.steam_name(appid)
        out["appid"] = appid
    else:
        short = game.shortcut_by_exe(d.get("exe") or "")
        if short:
            out["game"] = short
    if re.search(r"launcher|ersc|seamless|modengine|reshade", os.path.basename(d.get("exe") or ""), re.I):
        out["launcher"] = os.path.basename(d.get("exe") or "")
    pt = getattr(ctx, "proctrack", None)
    if pt and crash.get("pid"):
        try:
            for r in pt.list_exited(limit=2000).get("exited", []):
                if r.get("pid") == crash["pid"] and r.get("start"):
                    out["runtime"] = max(0, crash["ts"] - r["start"])
                    break
        except Exception:  # noqa: BLE001
            pass
    return out


def _boot_time() -> float:
    try:
        return psutil.boot_time()
    except Exception:  # noqa: BLE001
        return time.time()


def _home():
    from .win import HOME
    return HOME
