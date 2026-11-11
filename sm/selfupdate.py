"""Versão do painel × GitHub: mostra o changelog e avisa quando há versão nova.

A consulta não baixa nada: compara `__version__` com o de `sm/__init__.py` no repositório. A checagem acontece só quando
alguém abre o painel, no máximo a cada CHECK_EVERY, e pode ser desligada em [update] check = false.
Instalar a versão nova só acontece quando o usuário pede ("Atualizar agora" na aba ou no ícone perto do relógio): o painel
dispara a tarefa "SelfMonitoring Atualizar", que roda tools/atualizar-painel.ps1 a partir de Arquivos de Programas
(só administradores alteram). O andamento fica em data/update-status.json. (Windows Update e winget ficam em updates.py.)
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request

from . import __version__
from .config import ROOT

log = logging.getLogger(__name__)
CHANGELOG = ROOT / "CHANGELOG.md"
CHECK_EVERY = 6 * 3600
MIN_GAP = 30               # s entre verificações pedidas à mão
MAX_BYTES = 1_000_000
TIMEOUT = 8

# o que muda entre as versões Windows e Linux; o frontend só exibe
UPDATE_STEPS = ["C:\\win-self-monitoring\\atualizar.cmd"]
APPLY_TASK = "SelfMonitoring Atualizar"
APPLY_STATUS = ROOT / "data" / "update-status.json"
APPLY_STALE = 40 * 60       # s: "rodando" há mais que isso = a tarefa morreu no meio
UPDATE_NOTE = ("Ou, no PC, dois cliques nesse arquivo (o Windows pede permissão de administrador). "
               "Sua configuração, senha e históricos são mantidos.")


def parse_version(v: str | None) -> tuple[int, ...] | None:
    """'0.6.0-win' -> (0, 6, 0). Sufixo ignorado; None se não for uma versão."""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", v or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def compare(installed: str, latest: str | None) -> str:
    a, b = parse_version(installed), parse_version(latest)
    if a is None or b is None:
        return "unknown"
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    return "update" if b > a else "ahead" if b < a else "current"


def remote_version(init_py: str) -> str | None:
    m = re.search(r"""^__version__\s*=\s*["']([^"']+)["']""", init_py, re.M)
    return m.group(1) if m else None


def parse_changelog(text: str) -> list[dict]:
    """CHANGELOG.md -> [{version, date, items}]. Cada item junta as linhas de um tópico ou parágrafo."""
    out: list[dict] = []
    item: str | None = None

    def flush() -> None:
        nonlocal item
        if item is not None and out:
            out[-1]["items"].append(item.strip())
        item = None

    for line in text.splitlines():
        if line.startswith("## "):
            flush()
            # "## 0.12.0-win — 2026-11-09": a data é opcional
            m = re.match(r"(\S+)(?:\s+[—–-]\s+(\d{4}-\d{2}-\d{2}))?", line[3:].strip())
            out.append({"version": m.group(1) if m else line[3:].strip(), "date": m.group(2) if m else None, "items": []})
        elif not out or line.startswith("# "):
            continue
        elif line.startswith("- "):
            flush()
            item = line[2:]
        elif line.strip():
            item = f"{item} {line.strip()}" if item is not None else line.strip()
        else:
            flush()
    flush()
    return out


class SelfUpdate:
    def __init__(self, repo: str, enabled: bool = True, branch: str = "main", now=time.time) -> None:
        self.repo, self.enabled, self.branch, self.now = repo, enabled, branch, now
        self.latest: str | None = None
        self.remote_log: list[dict] | None = None
        self.checked = 0.0
        self.error: str | None = None
        self._lock = threading.Lock()
        self._busy = False

    def _get(self, path: str) -> str:
        url = f"https://raw.githubusercontent.com/{self.repo}/{self.branch}/{path}"
        req = urllib.request.Request(url, headers={"User-Agent": "self-monitoring"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read(MAX_BYTES).decode("utf-8", "replace")

    def check(self) -> None:
        """Consulta o GitHub. Em falha mantém o último resultado e guarda o motivo."""
        with self._lock:
            if self._busy:
                return
            self._busy = True
        try:
            latest = remote_version(self._get("sm/__init__.py"))
            if not latest:
                raise ValueError("versão não encontrada no repositório")
            self.latest, self.error = latest, None
            try:
                self.remote_log = parse_changelog(self._get("CHANGELOG.md")) or self.remote_log
            except (OSError, ValueError) as e:     # a versão vale mesmo sem o changelog novo
                log.debug("changelog do GitHub indisponível: %s", e)
        except urllib.error.HTTPError as e:
            self.error = f"O GitHub respondeu com erro {e.code}."
        except (OSError, ValueError) as e:
            log.debug("verificação de versão falhou: %s", e)
            self.error = "Não foi possível consultar o GitHub. Verifique a internet do PC."
        finally:
            self.checked = self.now()
            self._busy = False

    def check_now(self) -> None:
        if self.enabled and self.now() - self.checked >= MIN_GAP:
            self.check()

    # ---------- instalar (só quando o usuário pede) ----------
    def can_apply(self) -> bool:
        """A tarefa existe (criada pelo install.cmd a partir da 0.18.0-win)."""
        if getattr(self, "_task_ok", None) is None or self.now() - getattr(self, "_task_at", 0) > 300:
            try:
                r = subprocess.run(["schtasks", "/query", "/tn", APPLY_TASK], capture_output=True, timeout=10,
                                   creationflags=0x08000000)
                self._task_ok = r.returncode == 0
            except (OSError, subprocess.SubprocessError):     # schtasks travado ou ausente: sem o botão
                self._task_ok = False
            self._task_at = self.now()
        return bool(self._task_ok)

    def apply_status(self) -> dict | None:
        try:
            st = json.loads(APPLY_STATUS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(st, dict):
            return None
        if st.get("state") in ("starting", "running", "installing") and self.now() - (st.get("ts") or 0) > APPLY_STALE:
            st = {**st, "state": "error", "message": "A atualização não terminou. Veja data\\update.log ou rode o install.cmd no PC."}
        return st

    def apply(self) -> dict:
        """Dispara a tarefa de atualização (não espera: ela para e reinicia o painel)."""
        st = self.apply_status()
        if st and st.get("state") in ("starting", "running", "installing"):
            raise RuntimeError("Já há uma atualização em andamento.")
        if not self.can_apply():
            raise RuntimeError("A atualização pelo painel ainda não está instalada: rode o install.cmd uma vez no PC.")
        APPLY_STATUS.write_text(json.dumps({"state": "starting", "message": "Iniciando a atualização...",
                                            "from": __version__, "ts": int(self.now())}), encoding="utf-8")
        try:
            r = subprocess.run(["schtasks", "/run", "/tn", APPLY_TASK], capture_output=True, text=True, timeout=15,
                               creationflags=0x08000000)
        except (OSError, subprocess.SubprocessError) as e:
            APPLY_STATUS.unlink(missing_ok=True)
            raise RuntimeError(f"O Windows não iniciou a tarefa de atualização: {e}") from None
        if r.returncode:
            APPLY_STATUS.unlink(missing_ok=True)
            raise RuntimeError(f"O Windows não iniciou a tarefa de atualização: {(r.stderr or r.stdout).strip()[:200]}")
        log.info("atualização do painel pedida (de %s)", __version__)
        return self.status()

    def _local_log(self) -> list[dict]:
        try:
            return parse_changelog(CHANGELOG.read_text(encoding="utf-8"))
        except OSError:
            return []

    def status(self) -> dict:
        checking = self._busy
        if self.enabled and not checking and self.now() - self.checked >= CHECK_EVERY:
            threading.Thread(target=self.check, daemon=True, name="selfupdate").start()
            checking = True
        entries = self.remote_log or self._local_log()
        base = f"https://github.com/{self.repo}"
        return {
            "installed": __version__, "latest": self.latest, "enabled": self.enabled, "checking": checking,
            "state": "off" if not self.enabled else compare(__version__, self.latest),
            "checked": self.checked or None, "error": self.error,
            "repo_url": base, "changelog_url": f"{base}/blob/{self.branch}/CHANGELOG.md",
            "guide_url": f"{base}/blob/{self.branch}/ATUALIZAR.md",
            "steps": UPDATE_STEPS, "note": UPDATE_NOTE,
            "can_apply": self.can_apply(), "apply": self.apply_status(),
            "changelog": [{**e, "new": compare(__version__, e["version"]) == "update",
                           "installed": compare(__version__, e["version"]) == "current"} for e in entries],
        }
