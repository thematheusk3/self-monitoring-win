"""Log de eventos do Windows (o equivalente do journal): consultas da aba Logs, trechos dos incidentes e vigia ao vivo.

Lido pela API nativa (wevtapi.dll, via ctypes): EvtQuery com XPath nos canais System e Application, EvtRender
(XML do evento) e EvtFormatMessage (texto traduzido, como no Visualizador de Eventos).

Cada linha vira o mesmo formato do journal do Linux, para a interface ser a mesma:
  ts, prio (0-7, como o syslog: Crítico=2, Erro=3, Aviso=4, Informação=6, Detalhado=7), ident (fonte),
  pid, unit (canal), msg, cursor ("canal|recordid|ts"), event_id, channel, level.

"Boots" são os intervalos entre inícios do Windows (Kernel-General 12 no canal System).
O vigia (EventLogWatcher) lê a cada poucos segundos o que entrou depois do último EventRecordID de cada canal
(gravado no banco: não perde nada entre reinícios do painel) e passa avisos e piores pelas regras (rules.py).
"""
from __future__ import annotations

import ctypes
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from ctypes import wintypes
from datetime import datetime, timezone

log = logging.getLogger(__name__)

CHANNELS = ("System", "Application")
LEVEL_PRIO = {1: 2, 2: 3, 3: 4, 4: 6, 0: 6, 5: 7}         # nível do Windows -> prioridade estilo syslog
LEVEL_PT = {1: "Crítico", 2: "Erro", 3: "Aviso", 4: "Informação", 0: "Informação", 5: "Detalhado"}
PRIO_LEVELS = {2: "Level=1", 3: "Level<=2 and Level>=1", 4: "Level<=3 and Level>=1", 5: "Level<=3 and Level>=1",
               6: "Level<=4", 7: "Level<=5"}
RX_PROVIDER = re.compile(r"[\w .()\-/]{1,120}")
RX_CURSOR = re.compile(r"(System|Application)\|\d{1,20}\|\d{1,12}(\.\d{1,7})?")
RX_BOOT = re.compile(r"b\d{9,11}")
MAX_GREP = 200
FIRST_SINCE = 24 * 3600
POLL = 3.0
# o vigia lê avisos e piores e também os relatórios 1001 (tela azul é erro, mas o LiveKernelEvent da GPU é "informação")
WATCH_LEVELS = "(Level>=1 and Level<=3) or EventID=1001"
NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"

# ---------- wevtapi ----------
_wevt = None
EvtQueryChannelPath, EvtQueryReverseDirection = 0x1, 0x200
EvtRenderEventXml = 1
EvtFormatMessageEvent = 1
ERROR_NO_MORE_ITEMS, ERROR_INSUFFICIENT_BUFFER, ERROR_EVT_CHANNEL_NOT_FOUND = 259, 122, 15007


def _api():
    global _wevt
    if _wevt is None:
        w = ctypes.WinDLL("wevtapi.dll", use_last_error=True)
        H = wintypes.HANDLE
        w.EvtQuery.restype = H
        w.EvtQuery.argtypes = [H, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        w.EvtNext.restype = wintypes.BOOL
        w.EvtNext.argtypes = [H, wintypes.DWORD, ctypes.POINTER(H), wintypes.DWORD, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD)]
        w.EvtRender.restype = wintypes.BOOL
        w.EvtRender.argtypes = [H, H, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
                                ctypes.POINTER(wintypes.DWORD)]
        w.EvtOpenPublisherMetadata.restype = H
        w.EvtOpenPublisherMetadata.argtypes = [H, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        w.EvtFormatMessage.restype = wintypes.BOOL
        w.EvtFormatMessage.argtypes = [H, H, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                       wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        w.EvtClose.restype = wintypes.BOOL
        w.EvtClose.argtypes = [H]
        _wevt = w
    return _wevt


_publishers: dict[str, int | None] = {}
_pub_lock = threading.Lock()


def _publisher(name: str):
    with _pub_lock:
        if name not in _publishers:
            if len(_publishers) > 400:
                for h in _publishers.values():
                    if h:
                        _api().EvtClose(h)
                _publishers.clear()
            _publishers[name] = _api().EvtOpenPublisherMetadata(None, name, None, 0, 0) or None
        return _publishers[name]


def _render_xml(h) -> str:
    w = _api()
    used, props = wintypes.DWORD(), wintypes.DWORD()
    w.EvtRender(None, h, EvtRenderEventXml, 0, None, ctypes.byref(used), ctypes.byref(props))
    buf = ctypes.create_unicode_buffer(used.value // 2 + 1)
    if not w.EvtRender(None, h, EvtRenderEventXml, used.value, buf, ctypes.byref(used), ctypes.byref(props)):
        return ""
    return buf.value


def _format(h, provider: str) -> str | None:
    pub = _publisher(provider)
    if not pub:
        return None
    w = _api()
    used = wintypes.DWORD()
    w.EvtFormatMessage(pub, h, 0, 0, None, EvtFormatMessageEvent, 0, None, ctypes.byref(used))
    if not used.value:
        return None
    buf = ctypes.create_unicode_buffer(used.value + 1)
    if not w.EvtFormatMessage(pub, h, 0, 0, None, EvtFormatMessageEvent, used.value, buf, ctypes.byref(used)):
        return None
    return buf.value.strip() or None


def _ts_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_time(s: str) -> float:
    s = (s or "").rstrip("Z")
    if "." in s:
        head, frac = s.split(".", 1)
        s = f"{head}.{frac[:6]}"
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0


def parse_xml(xml: str) -> dict:
    """XML do evento -> campos brutos (System + EventData/UserData)."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return {}
    sysn = root.find(f"{NS}System")
    out: dict = {}
    if sysn is not None:
        prov = sysn.find(f"{NS}Provider")
        out["Provider"] = prov.get("Name") if prov is not None else ""
        out["EventSourceName"] = prov.get("EventSourceName") if prov is not None else None
        for tag in ("EventID", "Level", "Task", "Opcode", "Keywords", "EventRecordID", "Channel", "Computer"):
            n = sysn.find(f"{NS}{tag}")
            if n is not None:
                out[tag] = (n.text or "").strip()
        t = sysn.find(f"{NS}TimeCreated")
        out["TimeCreated"] = t.get("SystemTime") if t is not None else ""
        ex = sysn.find(f"{NS}Execution")
        if ex is not None:
            out["ProcessID"], out["ThreadID"] = ex.get("ProcessID"), ex.get("ThreadID")
        sec = sysn.find(f"{NS}Security")
        if sec is not None and sec.get("UserID"):
            out["UserID"] = sec.get("UserID")
    data: dict = {}
    ed = root.find(f"{NS}EventData")
    if ed is not None:
        for i, d in enumerate(ed.findall(f"{NS}Data")):
            data[d.get("Name") or f"param{i + 1}"] = (d.text or "").strip()
        bin_ = ed.find(f"{NS}Binary")
        if bin_ is not None and bin_.text:
            data["Binary"] = bin_.text[:200]
    ud = root.find(f"{NS}UserData")
    if ud is not None:
        for child in ud.iter():
            if child is not ud and len(child) == 0 and child.text and child.text.strip():
                data[child.tag.split("}")[-1]] = child.text.strip()
    out["Data"] = data
    return out


def normalize(raw: dict, msg: str | None = None) -> dict:
    """Campos brutos -> o que a interface mostra (mesmo formato do journal do Linux)."""
    try:
        level = int(raw.get("Level") or 4)
    except ValueError:
        level = 4
    ts = _parse_time(raw.get("TimeCreated", ""))
    ch = raw.get("Channel") or "System"
    rid = raw.get("EventRecordID") or "0"
    try:
        eid = int(raw.get("EventID") or 0)
    except ValueError:
        eid = 0
    try:
        pid = int(raw.get("ProcessID") or 0) or None
    except ValueError:
        pid = None
    data = raw.get("Data") or {}
    if not msg:
        vals = [v for v in data.values() if v]
        msg = " · ".join(vals)[:2000] if vals else f"Evento {eid}"
    provider = raw.get("Provider") or "?"
    ident = raw.get("EventSourceName") or provider
    return {"ts": ts, "prio": LEVEL_PRIO.get(level, 6), "ident": ident, "provider": provider, "pid": pid,
            "unit": ch, "msg": msg[:4000], "cursor": f"{ch}|{rid}|{ts:.6f}", "boot": None, "kernel": False,
            "event_id": eid, "channel": ch, "level": LEVEL_PT.get(level, str(level)), "record": int(rid or 0),
            "data": data}


def _read(channel: str, xpath: str, limit: int, reverse: bool = True, with_msg: bool = True) -> list[dict]:
    """Até `limit` eventos de um canal que casam com o XPath (mais novos primeiro se reverse)."""
    w = _api()
    flags = EvtQueryChannelPath | (EvtQueryReverseDirection if reverse else 0)
    q = w.EvtQuery(None, channel, xpath, flags)
    if not q:
        err = ctypes.get_last_error()
        if err not in (ERROR_EVT_CHANNEL_NOT_FOUND, 0):
            log.debug("EvtQuery(%s, %s) falhou: %s", channel, xpath, err)
        return []
    out: list[dict] = []
    try:
        arr = (wintypes.HANDLE * 64)()
        n = wintypes.DWORD()
        while len(out) < limit:
            if not w.EvtNext(q, min(64, limit - len(out)), arr, 2000, 0, ctypes.byref(n)):
                break
            for i in range(n.value):
                h = arr[i]
                try:
                    raw = parse_xml(_render_xml(h))
                    if raw:
                        msg = _format(h, raw.get("Provider", "")) if with_msg else None
                        out.append(normalize(raw, msg))
                finally:
                    w.EvtClose(h)
    finally:
        w.EvtClose(q)
    return out


def _xpath(conds: list[str]) -> str:
    conds = [c for c in conds if c]
    return f"*[System[{' and '.join(conds)}]]" if conds else "*"


def _time_cond(since: float | None = None, until: float | None = None) -> str:
    parts = []
    if since is not None:
        parts.append(f"@SystemTime>='{_ts_iso(max(0.0, since))}'")
    if until is not None:
        parts.append(f"@SystemTime<='{_ts_iso(until)}'")
    return f"TimeCreated[{' and '.join(parts)}]" if parts else ""


def _key(m: dict) -> tuple:
    return (m["ts"], m["channel"], m["record"])


def _cursor_key(c: str) -> tuple:
    if not RX_CURSOR.fullmatch(c or ""):
        raise ValueError("Cursor inválido.")
    ch, rid, ts = c.split("|")
    return (float(ts), ch, int(rid))


# ---------- consultas (aba Logs) ----------
def query(q: dict) -> dict:
    """Filtros (os mesmos do Linux): grep, unit (fonte do evento), priority (2-7), kernel (só o canal Sistema),
    boot ("0", "b<epoch>" ou "all"), since, until (epoch), limit, before/after (cursor de uma linha)."""
    limit = max(1, min(int(q.get("limit") or 300), 2000))
    conds: list[str] = []
    if q.get("priority") not in (None, ""):
        p = max(0, min(7, int(q["priority"])))
        conds.append(PRIO_LEVELS.get(max(2, p), "Level<=4"))
    src = q.get("unit") or q.get("ident")
    if src:
        if not RX_PROVIDER.fullmatch(src) or "'" in src:
            raise ValueError("Fonte inválida.")
        conds.append(f"(Provider[@Name='{src}'] or Provider[@EventSourceName='{src}'])")
    since = float(q["since"]) if q.get("since") else None
    until = float(q["until"]) if q.get("until") else None
    boot = q.get("boot")
    if boot and boot != "all":
        b = _boot_range(boot)
        if b:
            since = max(since or 0, b[0]) if since else b[0]
            until = min(until, b[1]) if until and b[1] else (until or b[1])
    grep = None
    if q.get("grep"):
        g = str(q["grep"])[:MAX_GREP]
        try:
            grep = re.compile(g, re.I)
        except re.error:
            grep = re.compile(re.escape(g), re.I)
    channels = ("System",) if q.get("kernel") else CHANNELS
    newer = bool(q.get("after"))
    ref = _cursor_key(q["after"] if newer else q["before"]) if (newer or q.get("before")) else None
    if ref and newer:
        since = max(since or 0, ref[0] - 0.001)
    elif ref:
        until = min(until, ref[0] + 0.001) if until else ref[0] + 0.001
    xp = _xpath([*conds, _time_cond(since, until)])
    # com busca por texto, lê mais para filtrar depois (a busca é no texto já formatado)
    per = min(limit * (8 if grep else 1) + 1, 5000)
    rows: list[dict] = []
    for ch in channels:
        rows += _read(ch, xp, per, reverse=not newer)
    if grep:
        rows = [m for m in rows if grep.search(m["msg"]) or grep.search(m["ident"]) or grep.search(str(m["event_id"]))]
    if ref:
        rows = [m for m in rows if (_key(m) > ref if newer else _key(m) < ref)]
    rows.sort(key=_key)
    more = False
    if newer:
        rows = rows[:limit]
    else:
        more = len(rows) > limit
        rows = rows[-limit:]
    for m in rows:
        m.pop("data", None)
    return {"items": rows, "newer": newer, "more": more}


def entry(cursor: str) -> dict | None:
    ts, ch, rid = _cursor_key(cursor)
    rows = _read(ch, f"*[System[EventRecordID={rid}]]", 1)
    if not rows:
        return None
    m = rows[0]
    data = m.pop("data", {}) or {}
    fields = {"Canal": m["channel"], "Fonte": m["provider"], "ID do evento": m["event_id"], "Nível": m["level"],
              "Registro": m["record"], "PID": m["pid"] or "", **{f"Dado: {k}": v for k, v in data.items()}}
    return {**m, "fields": fields}


# ---------- boots ----------
_boots_cache: tuple[float, list[dict]] = (0.0, [])


def boots(limit: int = 30) -> list[dict]:
    """Inícios do Windows (Kernel-General 12), do mais novo para o mais antigo. id = "b<epoch do início>"."""
    global _boots_cache
    if time.time() - _boots_cache[0] < 60:
        return _boots_cache[1][:limit]
    starts = _read("System", "*[System[Provider[@Name='Microsoft-Windows-Kernel-General'] and EventID=12]]",
                   limit + 1, with_msg=False)
    import psutil
    boot_now = psutil.boot_time()
    times = sorted({round(m["ts"]) for m in starts}, reverse=True)
    if not times or abs(times[0] - boot_now) > 600:
        times.insert(0, round(boot_now))
    out = []
    for i, t in enumerate(times[:limit]):
        nxt = times[i - 1] if i else None
        out.append({"index": -i, "id": f"b{int(t)}", "first": float(t), "last": float(nxt) if nxt else time.time()})
    _boots_cache = (time.time(), out)
    return out


def _boot_range(boot: str) -> tuple[float, float | None] | None:
    bs = boots()
    if boot in ("0", "-0", ""):
        return (bs[0]["first"], None) if bs else None
    if re.fullmatch(r"-?\d{1,3}", boot):
        i = abs(int(boot))
        return (bs[i]["first"], bs[i]["last"]) if i < len(bs) else None
    if not RX_BOOT.fullmatch(boot):
        raise ValueError("Boot inválido.")
    for b in bs:
        if b["id"] == boot:
            return (b["first"], b["last"] if b["index"] else None)
    t = float(boot[1:])
    return (t, None)


def window(t0: float, t1: float, limit: int = 400, boot: str | None = None) -> list[dict]:
    """Avisos e piores dos dois canais entre t0 e t1 (trecho de log dos incidentes)."""
    xp = _xpath(["Level<=3 and Level>=1", _time_cond(t0, t1 + 1)])
    rows = []
    for ch in CHANNELS:
        rows += _read(ch, xp, limit)
    rows.sort(key=_key)
    return rows[-limit:]


def around(t0: float, t1: float, limit: int = 300) -> list[dict]:
    """Tudo (qualquer nível) entre t0 e t1."""
    xp = _xpath([_time_cond(t0, t1)])
    rows = []
    for ch in CHANNELS:
        rows += _read(ch, xp, limit)
    rows.sort(key=_key)
    return rows[-limit:]


def service_logs(name: str, display: str | None, n: int = 80) -> list[dict]:
    """Linhas de um serviço: as do Service Control Manager que o citam e as que ele mesmo gravou como fonte."""
    rows = []
    scm = _read("System", "*[System[Provider[@Name='Service Control Manager']]]", 400)
    targets = {x.lower() for x in (name, display) if x}
    rows += [m for m in scm if any(str(v).lower() in targets for v in (m.get("data") or {}).values())]
    if RX_PROVIDER.fullmatch(name) and "'" not in name:
        for ch in CHANNELS:
            rows += _read(ch, f"*[System[Provider[@Name='{name}']]]", n)
    rows.sort(key=_key)
    for m in rows:
        m.pop("data", None)
    return rows[-n:]


# ---------- vigia ao vivo ----------
class EventLogWatcher:
    """Lê os eventos novos dos canais System e Application (avisos e piores + relatórios 1001) e passa pelas regras."""

    def __init__(self, store, on_event) -> None:
        from . import rules
        self.rules = rules
        self.store = store
        self.on_event = on_event          # (evento, id) -> None
        self._stop = threading.Event()
        self.last: dict[str, int] = {}
        for ch in CHANNELS:
            try:
                self.last[ch] = int(store.get_meta(f"eventlog_{ch}") or 0)
            except ValueError:
                self.last[ch] = 0
        self.seen = 0
        self.matched = 0
        self.backlog_done = False
        self.error: str | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, daemon=True, name="eventlog")
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        return {"running": bool(self.thread and self.thread.is_alive()), "seen": self.seen, "matched": self.matched,
                "backlog_done": self.backlog_done, "error": self.error}

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll()
                self.error = None
            except Exception as e:  # noqa: BLE001
                self.error = str(e)
                log.exception("vigia do log de eventos falhou")
            self.backlog_done = True
            self._stop.wait(POLL)

    def poll(self) -> int:
        new = []
        for ch in CHANNELS:
            last = self.last.get(ch, 0)
            if last:
                xp = f"*[System[EventRecordID>{last} and ({WATCH_LEVELS})]]"
                rows = _read(ch, xp, 2000, reverse=False)
                # o EventRecordID avança com qualquer evento; guarda o maior visto, mesmo sem aviso
                top = _read(ch, "*", 1, with_msg=False)
            else:
                xp = _xpath([f"({WATCH_LEVELS})", _time_cond(time.time() - FIRST_SINCE)])
                rows = _read(ch, xp, 2000, reverse=False)
                top = _read(ch, "*", 1, with_msg=False)
            if top and top[0]["record"] < last:      # o log foi limpo: recomeça
                rows = []
            new += rows
            if top:
                self.last[ch] = top[0]["record"]
            self.store.set_meta(f"eventlog_{ch}", str(self.last.get(ch, 0)))
        new.sort(key=_key)
        for m in new:
            self.seen += 1
            self.handle(m)
        return len(new)

    def handle(self, m: dict) -> None:
        ev = self.rules.classify(m)
        if not ev:
            return
        self.matched += 1
        try:
            eid = self.store.add_event(ev)
        except Exception:  # noqa: BLE001
            log.exception("falha ao gravar evento")
            return
        try:
            self.on_event(ev, eid)
        except Exception:  # noqa: BLE001
            log.exception("investigador falhou com o evento %s", ev.get("kind"))
