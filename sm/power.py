"""Desligar / reiniciar a máquina pelo painel (shutdown.exe).

O painel roda na sessão do usuário, que já tem permissão de desligar o PC.

force=True (/f): fecha os programas sem esperar, ignorando os que bloqueiam o desligamento
("deseja salvar?", downloads, jogos) e outros usuários logados; dados não salvos podem se perder.
Sem forçar, o Windows pode mostrar "Este app está impedindo o desligamento" e esperar.
"""
from __future__ import annotations

import logging
import threading
import time

from .win import SYSTEMROOT, run

log = logging.getLogger(__name__)
ACTIONS = {"poweroff": "/s", "reboot": "/r"}
DELAY = 2.0   # s: dá tempo da resposta HTTP chegar ao navegador
SHUTDOWN = str(SYSTEMROOT / "System32" / "shutdown.exe")


class PowerError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def status() -> dict:
    return {a: "yes" for a in ACTIONS}


def request(action: str, force: bool) -> dict:
    if action not in ACTIONS:
        raise PowerError("Ação inválida.")
    # /d p:0:0 = "outro (planejado)" no registro de eventos; /t 0 = agora
    cmd = [SHUTDOWN, ACTIONS[action], "/t", "0", "/d", "p:0:0"]
    if force:
        cmd.append("/f")
    log.warning("energia: %s solicitado pelo painel (force=%s)", action, force)

    def go():
        time.sleep(DELAY)
        r = run(cmd, timeout=30)
        if r.returncode:
            log.error("energia: '%s' falhou: %s", " ".join(cmd), (r.stderr or r.stdout).strip())

    threading.Thread(target=go, daemon=True, name="power").start()
    return {"ok": True, "action": action, "force": force, "in": DELAY}


# ---------- timer ----------
class PowerTimer:
    """Desliga/reinicia num horário. Sempre forçado (ignora o que estiver bloqueando).
    Persistido em data/power-timer.json: sobrevive a reinícios do serviço."""

    WARN_AT = (300, 60)   # avisos na tela do PC: 5 min e 1 min antes

    def __init__(self) -> None:
        from .config import ROOT
        self.file = ROOT / "data" / "power-timer.json"
        self.lock = threading.Lock()
        self.timer: dict | None = None
        self._warned: set[int] = set()
        try:
            t = __import__("json").loads(self.file.read_text())
            if t.get("at", 0) > time.time() - 120:   # passou do horário com o serviço parado há pouco: executa
                self.timer = t
            else:
                self.file.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
        threading.Thread(target=self._loop, daemon=True, name="power-timer").start()

    def status(self) -> dict | None:
        with self.lock:
            if not self.timer:
                return None
            return {**self.timer, "seconds_left": max(0, round(self.timer["at"] - time.time()))}

    def schedule(self, action: str, minutes: float) -> dict:
        import json
        if action not in ACTIONS:
            raise PowerError("Ação inválida.")
        try:
            minutes = float(minutes)
        except (TypeError, ValueError):
            raise PowerError("Tempo inválido.") from None
        if not 1 <= minutes <= 24 * 60:
            raise PowerError("O timer deve ser de 1 minuto a 24 horas.")
        with self.lock:
            self.timer = {"action": action, "at": time.time() + minutes * 60, "created": time.time()}
            self._warned = {w for w in self.WARN_AT if w >= minutes * 60}
            self.file.write_text(json.dumps(self.timer))
        self._notify(f"{'Desligamento' if action == 'poweroff' else 'Reinício'} agendado pelo painel",
                     f"O PC vai {VERB[action]} às {time.strftime('%H:%M', time.localtime(self.timer['at']))} "
                     f"(em {_fmt(minutes * 60)}), sem esperar programas abertos.")
        log.warning("energia: %s agendado para daqui %.0f min", action, minutes)
        return self.status()

    def cancel(self) -> None:
        with self.lock:
            had, self.timer = self.timer, None
            self.file.unlink(missing_ok=True)
        if had:
            self._notify("Timer cancelado", f"O {'desligamento' if had['action'] == 'poweroff' else 'reinício'} "
                                            "agendado pelo painel foi cancelado.")

    def _loop(self) -> None:
        while True:
            time.sleep(2)
            with self.lock:
                t = self.timer
            if not t:
                continue
            left = t["at"] - time.time()
            for w in self.WARN_AT:
                if left <= w and w not in self._warned:
                    self._warned.add(w)
                    self._notify("O PC vai " + VERB[t["action"]], f"Em {_fmt(left)}. Salve o que estiver fazendo.",
                                 urgency="critical")
            if left <= 0:
                with self.lock:
                    self.timer = None
                    self.file.unlink(missing_ok=True)
                try:
                    request(t["action"], force=True)
                except PowerError as e:
                    log.error("energia: timer não conseguiu %s: %s", t["action"], e)

    @staticmethod
    def _notify(title: str, body: str, urgency: str = "normal") -> None:
        try:
            from .desktop import notify_pc
            notify_pc(title, body, urgency=urgency)
        except Exception:  # noqa: BLE001  (aviso é opcional; o timer não pode falhar por isso)
            pass


VERB = {"poweroff": "desligar", "reboot": "reiniciar"}


def _fmt(sec: float) -> str:
    sec = int(round(sec))
    h, m = divmod(sec // 60, 60)
    if h:
        return f"{h}h{m:02d}"
    if sec >= 60:
        return f"{sec // 60} min"
    return f"{sec} s"
