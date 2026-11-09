"""Atualizações pendentes (somente consulta; o painel não instala nada).

- Windows Update: a mesma busca das Configurações (API COM Microsoft.Update.Session), inclui drivers.
- winget: programas instalados com versão nova disponível (`winget upgrade`).
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
import winreg

from .config import ROOT
from .win import powershell_json, run

log = logging.getLogger(__name__)
CACHE = ROOT / "data" / "updates.json"
AUTO_EVERY = 6 * 3600

WU_SCRIPT = (
    "$s = New-Object -ComObject Microsoft.Update.Session; "
    "$r = $s.CreateUpdateSearcher().Search('IsInstalled=0 and IsHidden=0'); "
    "foreach ($u in $r.Updates) { [pscustomobject]@{Title=$u.Title; Kb=($u.KBArticleIDs -join ','); "
    "Type=[int]$u.Type; Cats=(($u.Categories | ForEach-Object { $_.Name }) -join ', '); Reboot=[bool]$u.RebootRequired} }"
)


def windows_update() -> tuple[list[dict], str | None]:
    try:
        rows = powershell_json(WU_SCRIPT, timeout=900)
    except OSError as e:
        return [], f"Windows Update: {e}"
    items = []
    for r in rows:
        title = str(r.get("Title") or "?")
        kb = r.get("Kb")
        driver = r.get("Type") == 2 or "Driver" in str(r.get("Cats") or "")
        items.append({"name": title, "current": None, "new": f"KB{kb}" if kb else None, "source": "Windows Update",
                      "sensitive": "driver" if driver else "sistema" if re.search(
                          r"Cumulative|Cumulativa|Feature update|Atualização de recursos|\bWindows 1[01]\b", title, re.I)
                      else None,
                      "reboot": bool(r.get("Reboot"))})
    return items, None


def parse_winget(text: str) -> list[dict]:
    """Tabela do `winget upgrade` (cabeçalho traduzido; colunas por posição)."""
    lines = [ln.rsplit("\r", 1)[-1].rstrip() for ln in text.splitlines()]
    dash = next((i for i, ln in enumerate(lines) if re.fullmatch(r"-{10,}", ln.strip())), None)
    if not dash:
        return []
    starts = [m.start() for m in re.finditer(r"\S+", lines[dash - 1])]
    if len(starts) < 4:
        return []
    out = []
    for ln in lines[dash + 1:]:
        if not ln.strip() or len(ln) < starts[3]:
            break               # fim da tabela ("N atualizações disponíveis.")
        cols = [ln[a:b].strip() for a, b in zip(starts, starts[1:] + [len(ln)])]
        out.append({"name": cols[0], "id": cols[1], "current": cols[2], "new": cols[3], "source": "winget",
                    "sensitive": "driver" if re.search(r"driver|nvidia|amd software|radeon", cols[0], re.I) else None})
    return out


def check() -> dict:
    items: list[dict] = []
    errors: list[str] = []
    wu, err = windows_update()
    items += wu
    if err:
        errors.append(err)
    if shutil.which("winget"):
        try:
            r = run(["winget", "upgrade", "--include-unknown", "--accept-source-agreements", "--disable-interactivity"],
                    timeout=180)
            items += parse_winget(r.stdout)
        except OSError as e:
            errors.append(f"winget falhou: {e}")
    else:
        errors.append("winget não encontrado (instale o 'Instalador de Aplicativo' da Microsoft Store).")
    kernel = any(i["source"] == "Windows Update" and i["sensitive"] == "sistema" for i in items)
    return {"items": items, "count": len(items), "checked": time.time(), "errors": errors, "kernel": kernel,
            "reboot_items": any(i.get("reboot") for i in items)}


REBOOT_KEYS = (
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending", "sistema"),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired", "sistema"),
)


def reboot_pending() -> dict | None:
    """O Windows já instalou algo que só vale depois de reiniciar (mesmas chaves que o Windows Update consulta)."""
    for path, reason in REBOOT_KEYS:
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path))
            return {"reason": reason, "running": None, "installed": None}
        except OSError:
            continue
    return None


class Updates:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        try:
            self.data = json.loads(CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = None
        threading.Thread(target=self._loop, daemon=True, name="updates").start()

    def status(self) -> dict:
        with self.lock:
            d = {"running": self.running, **(self.data or {"items": [], "count": None, "checked": None})}
        if "reboot_items" not in d:        # cache de versões antigas: "reboot" era se algum item pedia reinício
            d["reboot_items"] = bool(d.get("reboot"))
        d["reboot"] = reboot_pending()
        return d

    def refresh(self) -> None:
        with self.lock:
            if self.running:
                return
            self.running = True
        threading.Thread(target=self._do, daemon=True, name="updates-check").start()

    def _do(self) -> None:
        try:
            d = check()
            CACHE.write_text(json.dumps(d), encoding="utf-8")
            with self.lock:
                self.data = d
            log.info("atualizações pendentes: %d", d["count"])
        except Exception:  # noqa: BLE001
            log.exception("falha ao verificar atualizações")
        finally:
            with self.lock:
                self.running = False

    def _loop(self) -> None:
        time.sleep(120)   # não pesa no logon
        while True:
            with self.lock:
                last = (self.data or {}).get("checked") or 0
            if time.time() - last > AUTO_EVERY:
                self.refresh()
            time.sleep(600)
