"""Carrega configuração de config.toml (com defaults)."""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.toml"


@dataclass
class Config:
    host: str = "0.0.0.0"
    port: int = 8765
    sample_interval: float = 1.0      # segundos entre amostras de métricas
    process_interval: float = 2.0     # segundos entre varreduras de processos
    history_seconds: int = 300        # quanto histórico manter para os gráficos
    auth_enabled: bool = True
    allow_kill: bool = True
    allow_power: bool = True             # desligar/reiniciar pelo painel
    allow_lock: bool = True              # bloquear a sessão (tela de bloqueio) pelo painel
    allow_launch: bool = True            # abrir aplicativos instalados pelo painel
    allow_screenshot: bool = True        # captura de tela (sempre avisa na tela do PC)
    allow_control: bool = True           # áudio e tela (volume, saída, resolução, brilho)
    disabled_collectors: list[str] = field(default_factory=list)
    # histórico de processos
    track_exited: bool = True
    track_interval: float = 0.5          # s entre varreduras da lista de processos
    exited_max: int = 3000               # quantos encerrados guardar
    track_persist: bool = True           # salva o histórico em data/exited.json
    net_monitor: bool = True             # monitor de conexões (aba Rede)
    net_interval: float = 2.0
    net_closed_max: int = 3000
    # sensores (LibreHardwareMonitor)
    lhm_url: str = "http://127.0.0.1:8085/data.json"
    # FPS (PresentMon)
    presentmon: str = ""                 # caminho do PresentMon; vazio = procura em tools/
    # aviso de versão nova (consulta o GitHub; não instala nada)
    update_check: bool = True
    update_repo: str = "thematheusk3/self-monitoring-win"


def load(path: Path | None = None) -> Config:
    path = path or Path(os.environ.get("SM_CONFIG", DEFAULT_CONFIG))
    raw: dict = {}
    if path.exists():
        # utf-8-sig: aceita o arquivo salvo com BOM (Bloco de Notas antigo, PowerShell 5)
        raw = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    server = raw.get("server", {})
    sampling = raw.get("sampling", {})
    security = raw.get("security", {})
    collectors = raw.get("collectors", {})
    sensors = raw.get("sensors", {})
    game = raw.get("game", {})
    update = raw.get("update", {})
    repo = str(update.get("repo", Config.update_repo))
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError(f'[update] repo inválido: "{repo}" (use o formato dono/repositorio)')

    cfg = Config(
        host=server.get("host", Config.host),
        port=int(os.environ.get("SM_PORT", server.get("port", Config.port))),
        sample_interval=float(sampling.get("interval", Config.sample_interval)),
        process_interval=float(sampling.get("process_interval", Config.process_interval)),
        history_seconds=int(sampling.get("history_seconds", Config.history_seconds)),
        auth_enabled=bool(security.get("auth", True)),
        allow_kill=bool(security.get("allow_kill", True)),
        allow_power=bool(security.get("allow_power", True)),
        allow_lock=bool(security.get("allow_lock", True)),
        allow_launch=bool(security.get("allow_launch", True)),
        allow_screenshot=bool(security.get("allow_screenshot", True)),
        allow_control=bool(security.get("allow_control", True)),
        disabled_collectors=list(collectors.get("disabled", [])),
        track_exited=bool(sampling.get("track_exited", True)),
        track_interval=max(0.2, float(sampling.get("track_interval", Config.track_interval))),
        exited_max=max(100, int(sampling.get("exited_max", Config.exited_max))),
        track_persist=bool(sampling.get("track_persist", True)),
        net_monitor=bool(sampling.get("net_monitor", True)),
        net_interval=max(1.0, float(sampling.get("net_interval", Config.net_interval))),
        net_closed_max=max(100, int(sampling.get("net_closed_max", Config.net_closed_max))),
        lhm_url=str(sensors.get("lhm_url", Config.lhm_url)),
        presentmon=str(game.get("presentmon", Config.presentmon)),
        update_check=bool(update.get("check", True)),
        update_repo=repo,
    )
    return cfg
