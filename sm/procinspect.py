"""Análise de processos e heurísticas de "pontos de atenção".

Nada aqui é um antivírus: são sinais comuns em malware para Windows (nome de processo do sistema
rodando fora do System32, executável em pasta temporária, PowerShell codificado, download-e-executa,
apagar cópias de sombra, minerador...). Cada sinal tem um nível: alto, médio ou info. Programas legítimos
também podem disparar alguns deles. O objetivo é chamar a atenção para investigar.

"De onde veio" o executável: assinatura digital (Authenticode, quem assinou e se é válida) e as
informações de versão do arquivo (descrição, empresa, produto).
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

import psutil

from .config import ROOT
from .win import HOME, is_admin, powershell_json, ps_quote, version_info

log = logging.getLogger(__name__)
TRUST_FILE = ROOT / "data" / "trusted.json"
WINDIR = os.environ.get("SystemRoot", r"C:\Windows").lower()
SYSTEM_DIRS = (WINDIR + "\\system32\\", WINDIR + "\\syswow64\\", WINDIR + "\\")
TEMP_DIRS = tuple({os.environ.get("TEMP", str(HOME / "AppData/Local/Temp")).lower() + "\\",
                   str(HOME / "AppData" / "Local" / "Temp").lower() + "\\", WINDIR + "\\temp\\"})
PROGRAM_DIRS = tuple(d.lower() + "\\" for d in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                                                os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")))
LOOSE_DIRS = {str(HOME / "AppData" / "Roaming").lower(), str(HOME / "AppData" / "Local").lower(),
              os.environ.get("ProgramData", r"C:\ProgramData").lower(), r"c:\users\public"}
DOWNLOADS = str(HOME / "Downloads").lower() + "\\"
# processos do Windows que malware costuma imitar; só existem em System32 (ou na pasta do Windows)
SYSTEM_NAMES = {"svchost.exe", "lsass.exe", "csrss.exe", "winlogon.exe", "services.exe", "smss.exe", "wininit.exe",
                "spoolsv.exe", "taskhostw.exe", "dllhost.exe", "rundll32.exe", "conhost.exe", "lsm.exe",
                "explorer.exe", "dwm.exe", "sihost.exe", "ctfmon.exe", "searchindexer.exe", "taskmgr.exe",
                "regsvr32.exe", "wmiprvse.exe", "runtimebroker.exe", "fontdrvhost.exe", "audiodg.exe"}
LOOKALIKE = re.compile(r"^(scvhost|svchosts|svhost|svch0st|svcchost|svchost32|lsas|lsasss|isass|csrsss|crss|"
                       r"expl0rer|explorer32|winlog0n|winlogin|smsss|spoolsvc|taskhost32|rundll|dllhst)\.exe$", re.I)
DOUBLE_EXT = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|jpe?g|png|gif|txt|mp3|mp4|avi|zip|rar)\.(exe|scr|com|bat|cmd|pif)$", re.I)
INTERPRETERS = {"powershell.exe", "pwsh.exe", "cmd.exe", "wscript.exe", "cscript.exe", "mshta.exe",
                "python.exe", "pythonw.exe", "node.exe", "rundll32.exe", "regsvr32.exe"}
SCRIPT_EXT = re.compile(r"\.(ps1|psm1|vbs|vbe|js|jse|wsf|hta|bat|cmd|py|pyw|dll)(,\S*)?$", re.I)
SIG_OK = "Valid"
SIG_NONE = "NotSigned"

CMD_RULES: list[tuple[str, str, re.Pattern]] = [
    ("alto", "PowerShell com comando codificado em base64 (técnica comum para esconder o que roda)",
     re.compile(r"\s-e(nc(odedcommand)?|c)?\s+[A-Za-z0-9+/=]{40,}", re.I)),
    ("alto", "Baixa algo da internet e executa direto na memória",
     re.compile(r"\b(iex|invoke-expression)\b.{0,120}(downloadstring|invoke-webrequest|\biwr\b|\birm\b|net\.webclient)|"
                r"(downloadstring|downloaddata)\s*\(.{0,200}\b(iex|invoke-expression)\b", re.I)),
    ("alto", "certutil usado para baixar ou decodificar arquivos (abuso comum do Windows)",
     re.compile(r"certutil(\.exe)?\s.*-(urlcache|decode|decodehex)\b", re.I)),
    ("alto", "bitsadmin usado para baixar arquivos",
     re.compile(r"bitsadmin(\.exe)?\s.*/(transfer|addfile)\b", re.I)),
    ("alto", "mshta executando script remoto ou embutido",
     re.compile(r"mshta(\.exe)?\W+\s*(https?:|javascript:|vbscript:)", re.I)),
    ("alto", "regsvr32/rundll32 executando script (técnica para contornar bloqueios)",
     re.compile(r"regsvr32.*(/i:\s*https?:|scrobj\.dll)|rundll32(\.exe)?\W.*javascript:", re.I)),
    ("alto", "Apaga cópias de sombra/backups do Windows (comportamento típico de ransomware)",
     re.compile(r"vssadmin.*delete\s+shadows|shadowcopy\s+delete|bcdedit.*recoveryenabled\s+no|"
                r"wbadmin\s+delete\s+(catalog|backup)", re.I)),
    ("alto", "Parece um minerador de criptomoeda",
     re.compile(r"stratum\+(tcp|ssl)://|\bxmrig\b|\bcpuminer\b|\bminerd\b|--donate-level|\bnicehash\b|"
                r"\bcryptonight\b|\brandomx\b", re.I)),
    ("alto", "Parece um shell reverso (envia um terminal pela rede)",
     re.compile(r"\bnc(at)?(\.exe)?\b.*\s-e\s+\S*(cmd|powershell)|net\.sockets\.tcpclient.{0,300}(getstream|iex)", re.I)),
    ("medio", "PowerShell com janela oculta acessando a internet",
     re.compile(r"-w(indowstyle)?\s+h(idden)?\b.*https?://|https?://.*-w(indowstyle)?\s+h(idden)?\b", re.I)),
    ("medio", "Apaga os logs de eventos do Windows",
     re.compile(r"wevtutil(\.exe)?\s+cl\b|clear-eventlog", re.I)),
    ("medio", "Mexe nas proteções do Windows Defender",
     re.compile(r"(set|add)-mppreference.*-(disablerealtimemonitoring|exclusionpath|exclusionprocess)", re.I)),
]

LEVEL_SCORE = {"alto": 50, "medio": 15, "info": 0}


# ---------- assinatura digital ----------
class Signatures:
    """Authenticode (Get-AuthenticodeSignature, que também entende arquivos do Windows assinados por
    catálogo), com cache por arquivo + data de modificação. Consultar custa ~20 ms por arquivo."""

    def __init__(self) -> None:
        self.cache: dict[tuple, dict] = {}
        self.lock = threading.Lock()
        self.pending: set[str] = set()
        self._worker: threading.Thread | None = None

    @staticmethod
    def _key(path: str) -> tuple | None:
        try:
            st = os.stat(path)
        except OSError:
            return None
        return (path.lower(), st.st_size, st.st_mtime_ns)

    def cached(self, path: str) -> dict | None:
        k = self._key(path)
        with self.lock:
            return self.cache.get(k) if k else None

    def lookup(self, paths: list[str], wait: bool = True) -> dict[str, dict | None]:
        """Assinatura de cada caminho. wait=False devolve só o que está no cache e consulta o resto em segundo plano."""
        out, todo = {}, []
        for p in dict.fromkeys(paths):
            k = self._key(p)
            if k is None:
                out[p] = None
                continue
            with self.lock:
                hit = self.cache.get(k)
            out[p] = hit
            if hit is None:
                todo.append(p)
        if todo and wait:
            self._query(todo)
            for p in todo:
                out[p] = self.cached(p)
        elif todo:
            with self.lock:
                self.pending.update(todo)
                if not self._worker or not self._worker.is_alive():
                    self._worker = threading.Thread(target=self._drain, daemon=True, name="signatures")
                    self._worker.start()
        return out

    def _drain(self) -> None:
        while True:
            with self.lock:
                batch = list(self.pending)[:60]
                self.pending.difference_update(batch)
            if not batch:
                return
            self._query(batch)

    def _query(self, paths: list[str]) -> None:
        for i in range(0, len(paths), 60):
            chunk = paths[i:i + 60]
            script = ("foreach ($f in @(" + ",".join(ps_quote(p) for p in chunk) + ")) { "
                      "$s = Get-AuthenticodeSignature -LiteralPath $f -ErrorAction SilentlyContinue; "
                      "[pscustomobject]@{Path=$f; Status=[string]$s.Status; "
                      "Signer=$(if ($s.SignerCertificate) { $s.SignerCertificate.GetNameInfo('SimpleName', $false) } else { $null })} }")
            res = {str(r.get("Path")).lower(): r for r in powershell_json(script, timeout=120)}
            with self.lock:
                for p in chunk:
                    k = self._key(p)
                    r = res.get(p.lower())
                    if k and r:
                        self.cache[k] = {"status": r.get("Status") or "UnknownError", "signer": r.get("Signer")}


SIGS = Signatures()


# ---------- hash ----------
_hash_cache: dict[tuple, str] = {}


def sha256_of(path: str, max_bytes: int = 512 * 1024 * 1024) -> tuple[str | None, str | None]:
    try:
        st = os.stat(path)
    except OSError as e:
        return None, f"não foi possível ler: {e.strerror}"
    if st.st_size > max_bytes:
        return None, "arquivo grande demais para calcular o hash"
    key = (path.lower(), st.st_size, st.st_mtime_ns)
    if key in _hash_cache:
        return _hash_cache[key], None
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError as e:
        return None, f"não foi possível ler: {e.strerror}"
    _hash_cache[key] = h.hexdigest()
    return _hash_cache[key], None


# ---------- confiáveis ----------
def load_trusted() -> set[str]:
    try:
        return set(json.loads(TRUST_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def set_trusted(exe: str, trusted: bool) -> set[str]:
    t = load_trusted()
    (t.add if trusted else t.discard)(exe)
    TRUST_FILE.write_text(json.dumps(sorted(t)), encoding="utf-8")
    return t


# ---------- heurísticas ----------
def _hidden(path: str) -> bool:
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(path)
    except (OSError, AttributeError):
        return False
    return attrs != -1 and attrs != 0xFFFFFFFF and bool(attrs & 0x2)


def flags_for(rec: dict, sig: dict | None, extra: dict | None = None) -> list[dict]:
    """Sinais a partir do registro (vivo ou encerrado). `sig` = assinatura (None = desconhecida);
    `extra` traz dados de análise ao vivo."""
    extra = extra or {}
    out: list[dict] = []
    add = lambda lvl, txt: out.append({"level": lvl, "text": txt})  # noqa: E731
    exe = rec.get("exe")
    name = rec.get("name") or ""
    lname = name.lower()
    cmd = rec.get("cmd") or ""
    status = (sig or {}).get("status")
    signed = status == SIG_OK
    unsigned = status == SIG_NONE

    if LOOKALIKE.match(name):
        add("alto", f"Nome imitando um processo do Windows ({name})")
    if DOUBLE_EXT.search(name):
        add("alto", "Extensão dupla no nome (ex.: documento.pdf.exe), truque para parecer um arquivo comum")

    if exe:
        low = exe.lower()
        folder = os.path.dirname(low)
        if extra.get("exe_missing"):
            add("alto", "O executável não existe mais no disco enquanto o processo roda")
        if lname in SYSTEM_NAMES and not low.startswith(SYSTEM_DIRS) and not rec.get("container"):
            add("alto", f"Usa o nome de um processo do Windows ({name}) mas roda de outra pasta ({os.path.dirname(exe)})")
        if low.startswith(TEMP_DIRS):
            if signed:
                add("info", "Executável em pasta temporária (assinado; comum em instaladores)")
            else:
                add("alto" if unsigned else "medio", f"Executável sem assinatura em pasta temporária ({os.path.dirname(exe)})")
        if folder in LOOSE_DIRS and not signed:
            add("medio", f"Executável solto direto em {os.path.dirname(exe)} (local comum de malware para se esconder)")
        if _hidden(exe):
            add("medio", "O executável é um arquivo oculto")
        if low.startswith(DOWNLOADS):
            add("info", "Executado direto da pasta Downloads")
        if status and status not in (SIG_OK, SIG_NONE, "UnknownError"):
            lvl = "alto" if status == "HashMismatch" else "medio"
            add(lvl, f"Assinatura digital inválida ({status}): o arquivo pode ter sido alterado depois de assinado")
        elif unsigned:
            if low.startswith(SYSTEM_DIRS):
                add("medio", "Arquivo na pasta do Windows sem assinatura digital")
            elif rec.get("user") == "SYSTEM" and not low.startswith(PROGRAM_DIRS):
                add("medio", "Roda como SYSTEM a partir de um executável sem assinatura fora de Arquivos de Programas")
            else:
                add("info", "Sem assinatura digital (comum em jogos indie, ferramentas e programas pequenos)")
        if rec.get("container"):
            add("info", "App da Microsoft Store (roda isolado, em pasta protegida do Windows)")

    # script de pasta temporária rodando em interpretador (o argumento é o arquivo, não só um texto citando a pasta)
    if lname in INTERPRETERS:
        args = [a.strip('"').lower() for a in re.findall(r'"[^"]+"|\S+', cmd)[1:]]
        if any(a.startswith(TEMP_DIRS) and SCRIPT_EXT.search(a) for a in args):
            add("alto", "Interpretador executando um script de pasta temporária")

    for lvl, txt, rx in CMD_RULES:
        if rx.search(cmd):
            add(lvl, txt)

    for lp in extra.get("listening", []):
        if lp["public"] and not signed:
            add("medio", f"Aceita conexões de outras máquinas na porta {lp['port']} e não tem assinatura válida")
            break
    if rec.get("peak_cpu", 0) >= 50 and unsigned and exe and not exe.lower().startswith(PROGRAM_DIRS):
        add("info", f"Uso alto de CPU ({rec['peak_cpu']:.0f}%) por programa sem assinatura")
    return out


def score(flags: list[dict]) -> int:
    return sum(LEVEL_SCORE[f["level"]] for f in flags)


# ---------- análise detalhada ----------
def _connections(p: psutil.Process) -> tuple[list[dict], list[dict]]:
    listening, remote = [], []
    try:
        conns = p.net_connections(kind="inet")
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return listening, remote
    for c in conns:
        if c.status == psutil.CONN_LISTEN and c.laddr:
            ip = c.laddr.ip
            listening.append({"ip": ip, "port": c.laddr.port,
                              "public": ip in ("0.0.0.0", "::") or not (ip.startswith("127.") or ip == "::1")})
        elif c.raddr:
            remote.append({"ip": c.raddr.ip, "port": c.raddr.port, "status": c.status,
                           "proto": "tcp" if c.type == 1 else "udp"})
    uniq = {(x["ip"], x["port"]): x for x in listening}
    return sorted(uniq.values(), key=lambda x: x["port"]), remote[:50]


def analyze_live(pid: int, create_time: float | None, base: dict | None) -> dict:
    p = psutil.Process(pid)
    if create_time is not None and abs(p.create_time() - float(create_time)) > 1:
        raise LookupError("O processo já terminou (PID reaproveitado).")
    with p.oneshot():
        rec = dict(base or {})
        rec.setdefault("pid", pid)
        rec["name"] = p.name()
        rec["ppid"] = p.ppid()
        try:
            cl = p.cmdline()
            rec["cmd"] = " ".join(cl)[:2000]
            rec["argv0"] = (cl or [None])[0]
        except (psutil.AccessDenied, OSError):
            pass
        try:
            rec["user"] = p.username().rsplit("\\", 1)[-1]
        except (psutil.AccessDenied, OSError):
            pass
        try:
            from .proctrack import real_path
            rec["exe"] = real_path(p.exe())
            rec["exe_verified"] = rec["exe"] is not None
        except (psutil.AccessDenied, OSError):
            rec.setdefault("exe", None)
            rec["exe_verified"] = False
        rec["start"] = p.create_time()
        info: dict = {"alive": True}
        for key, fn in (("cwd", p.cwd), ("threads", p.num_threads), ("fds", p.num_handles),
                        ("status", p.status), ("nice", p.nice)):
            try:
                info[key] = fn()
            except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
                info[key] = None
        try:
            info["rss"] = p.memory_info().rss
        except (psutil.AccessDenied, OSError):
            info["rss"] = None
        try:
            info["children"] = len(p.children())
        except psutil.Error:
            info["children"] = None
    info["listening"], info["remote"] = _connections(p)
    if rec.get("exe") and not os.path.exists(rec["exe"]):
        info["exe_missing"] = True
    from .proctrack import SERVICES
    rec["unit"] = SERVICES.get(pid)
    rec["container"] = bool(rec.get("exe") and "\\windowsapps\\" in rec["exe"].lower())
    return _finish(rec, info)


def analyze_record(rec: dict) -> dict:
    """Processo encerrado: só o que foi capturado + análise do arquivo, se ainda existir."""
    return _finish(dict(rec), {"alive": False, "listening": [], "remote": []})


def _finish(rec: dict, info: dict) -> dict:
    exe = rec.get("exe")
    sig = SIGS.lookup([exe])[exe] if exe else None
    file = None
    vi = {}
    if exe:
        sha, err = sha256_of(exe)
        try:
            st = os.stat(exe)
            file = {"path": exe, "size": st.st_size, "mtime": st.st_mtime, "exists": True, "hidden": _hidden(exe)}
            vi = version_info(exe)
        except OSError:
            file = {"path": exe, "exists": False}
        file["sha256"], file["hash_error"] = sha, err
        if not sha and rec.get("sha256"):     # capturado quando o processo apareceu
            file["sha256"], file["hash_error"] = rec["sha256"], None
            file["note"] = "hash calculado quando o processo rodava"
    flags = flags_for(rec, sig, info)
    trusted = exe in load_trusted() if exe else False
    return {
        "process": rec, "info": info, "exe": exe, "exe_guessed": False, "deleted": bool(info.get("exe_missing")),
        "signature": sig, "version_info": vi or None, "file": file,
        "flags": flags, "score": score(flags), "trusted": trusted,
        "visibility": "full" if rec.get("exe_verified") or is_admin() else "limited",
    }


# ---------- lista de suspeitos ----------
_susp_cache: dict = {"ts": 0, "data": None}
_susp_lock = threading.Lock()


def suspicious(tracker, include_info: bool = False) -> dict:
    """Varre os processos vivos e encerrados com heurísticas baratas (sem hash). As assinaturas que ainda
    não estão no cache são consultadas em segundo plano e entram nas próximas varreduras."""
    with _susp_lock:
        if time.time() - _susp_cache["ts"] < 3 and _susp_cache["data"] and not include_info:
            return _susp_cache["data"]
        with tracker._lock:
            live = [dict(r) for r in tracker.live.values()]
            dead = [dict(r) for r in tracker.exited]
        for r in live:
            r["_alive"] = True
        for r in dead:
            r["_alive"] = False
        allrecs = live + dead
        sigs = SIGS.lookup([r["exe"] for r in allrecs if r.get("exe")], wait=False)
        trusted = load_trusted()
        items = []
        for r in allrecs:
            exe = r.get("exe")
            if exe and exe in trusted:
                continue
            extra = {}
            if r["_alive"] and exe and not os.path.exists(exe):
                extra["exe_missing"] = True
            fl = flags_for(r, sigs.get(exe) if exe else None, extra)
            sc = score(fl)
            if (sc == 0 and not include_info) or not fl:
                continue
            items.append({
                "id": r["id"], "pid": r["pid"], "name": r["name"], "user": r["user"],
                "cmd": r["cmd"], "exe": exe, "alive": r["_alive"], "start": r["start"],
                "end": r.get("end"), "create_time": r["start"],
                "flags": fl, "score": sc,
            })
        # agrupa repetições do mesmo executável + comando
        grouped: dict[tuple, dict] = {}
        for it in sorted(items, key=lambda x: (x["alive"], x["start"]), reverse=True):
            k = (it["exe"], it["cmd"])
            if k in grouped:
                grouped[k]["count"] += 1
            else:
                grouped[k] = {**it, "count": 1}
        res = sorted(grouped.values(), key=lambda x: (-x["score"], not x["alive"], -x["start"]))
        with SIGS.lock:
            pending = len(SIGS.pending)
        data = {"items": res, "trusted": sorted(trusted), "package_tool": "authenticode",
                "limited": not is_admin(), "pending_signatures": pending}
        if not include_info:
            _susp_cache.update(ts=time.time(), data=data)
        return data
