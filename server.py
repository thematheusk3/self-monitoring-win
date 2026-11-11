#!/usr/bin/env python3
"""Ponto de entrada: python server.py [--port N] [--host H]"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import signal
import socket
import subprocess
import sys
from pathlib import Path

# o Windows não usa UTF-8 por padrão para arquivos e subprocessos: reinicia no modo UTF-8 do Python
if not sys.flags.utf8_mode and __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-X", "utf8", *sys.argv]))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sm import __version__, config  # noqa: E402
from sm.win import enable_dpi_awareness  # noqa: E402

SKIP_IFACES = ("Loopback", "vEthernet", "isatap", "Teredo", "Bluetooth")


def lan_ips() -> list[str]:
    import psutil
    ips = []
    stats = psutil.net_if_stats()
    for name, addrs in psutil.net_if_addrs().items():
        if name.startswith(SKIP_IFACES) or not (stats.get(name) and stats[name].isup):
            continue
        ips += [a.address for a in addrs if a.family == socket.AF_INET
                and not a.address.startswith(("127.", "169.254."))]
    return ips


def set_password() -> None:
    import getpass
    from sm.auth import MIN_LEN, Auth
    auth = Auth()
    print("Trocar senha do self-monitoring" if auth.has_password() else "Cadastrar senha do self-monitoring")
    while True:
        pw = getpass.getpass("Nova senha: ")
        if len(pw) < MIN_LEN:
            print(f"  A senha precisa ter pelo menos {MIN_LEN} caracteres.")
            continue
        if pw != getpass.getpass("Repita a senha: "):
            print("  As senhas não conferem.")
            continue
        break
    auth.set_password(pw)
    auth.end_all_except(None)
    print("Senha salva. Todas as sessões abertas foram encerradas.")


def start_history(ctx, cfg) -> None:
    """Banco persistente + inventário de hardware + métricas por minuto + vigia do log de eventos/incidentes."""
    log = logging.getLogger("self-monitoring")
    from sm import fleet
    from sm.hwinfo import HardwareInventory
    from sm.recorder import MetricsRecorder
    from sm.store import Store
    try:
        ctx.store = Store(fleet.machine_id())
    except Exception:  # noqa: BLE001
        log.exception("banco de histórico indisponível; recursos de histórico desativados")
        return
    ctx.hwinv = HardwareInventory(ctx.store)
    ctx.hwinv.start()
    ctx.recorder = MetricsRecorder(ctx, ctx.store, cfg.history_days)
    ctx.recorder.start()
    if cfg.incidents:
        from sm.eventlog import EventLogWatcher
        from sm.incidents import Investigator
        ctx.investigator = Investigator(ctx, ctx.store)
        ctx.investigator.start()
        ctx.journal = EventLogWatcher(ctx.store, ctx.investigator.on_event)
        ctx.journal.start()


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.handlers.RotatingFileHandler(
        config.ROOT / "data" / "self-monitoring.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")]
    if sys.stderr is not None:          # com console (python.exe); com pythonw não há onde escrever
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=level, format=fmt, handlers=handlers)


def main() -> None:
    ap = argparse.ArgumentParser(description="self-monitoring")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--config", type=Path)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--set-password", action="store_true", help="cadastra/troca a senha de acesso e sai")
    args = ap.parse_args()

    (config.ROOT / "data").mkdir(exist_ok=True)   # num clone novo ainda não existe
    if args.set_password:
        return set_password()

    setup_logging(args.verbose)
    log = logging.getLogger("self-monitoring")
    enable_dpi_awareness()
    cfg = config.load(args.config)
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port

    from sm.sampler import Context, Sampler
    from sm.server import make_server
    try:
        srv_probe = socket.socket()
        srv_probe.bind((cfg.host, cfg.port))
        srv_probe.close()
    except OSError:
        log.error("a porta %d já está em uso (outro self-monitoring rodando?)", cfg.port)
        sys.exit(1)

    ctx = Context(cfg)
    if cfg.track_exited:
        from sm.proctrack import ProcessTracker
        ctx.proctrack = ProcessTracker(ctx)
        ctx.proctrack.start()
    if cfg.net_monitor:
        from sm.netmon import NetMonitor
        ctx.netmon = NetMonitor(ctx)
        ctx.netmon.start()
    from sm.alerts import Alerts
    from sm.game import GameMonitor
    from sm.power import PowerTimer
    from sm.mural import Mural
    from sm.selfupdate import SelfUpdate
    from sm.updates import Updates
    from sm.webhook import Webhook
    ctx.game = GameMonitor(ctx)
    ctx.updates = Updates()
    ctx.selfupdate = SelfUpdate(cfg.update_repo, cfg.update_check)
    ctx.mural = Mural()
    ctx.power_timer = PowerTimer() if cfg.allow_power else None
    ctx.alerts = Alerts(ctx)
    ctx.alerts.start()
    ctx.webhook = Webhook(ctx.alerts)
    if cfg.history_enabled:
        start_history(ctx, cfg)
    Sampler(ctx).start()
    srv = make_server(ctx)
    if cfg.tray:
        from sm.tray import Tray
        ctx.tray = Tray(ctx, cfg.port, lan_ips)
        ctx.tray.start()

    from sm.win import is_admin
    log.info("self-monitoring %s | coletores: %s | administrador: %s", __version__,
             ", ".join(c.name for c in ctx.collectors), "sim" if is_admin() else "não")
    hosts = lan_ips() if cfg.host in ("0.0.0.0", "") else [cfg.host]
    for ip in hosts:
        log.info("acesse: http://%s:%d", ip, cfg.port)
    if not ctx.lhm.ok:
        log.warning("LibreHardwareMonitor não respondeu em %s: sem temperatura da CPU e sensores da placa "
                    "(veja o README)", cfg.lhm_url)
    if cfg.auth_enabled:
        from sm.auth import Auth
        if not Auth().has_password():
            log.warning("nenhuma senha cadastrada: rode  python server.py --set-password  "
                        "ou abra http://localhost:%d nesta máquina", cfg.port)

    def on_term(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_term)
    if hasattr(signal, "SIGBREAK"):            # Ctrl+Break / encerramento do console
        signal.signal(signal.SIGBREAK, on_term)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if getattr(ctx, "tray", None):
            ctx.tray.remove()                  # sem isso o ícone fica "fantasma" até passar o mouse
        if ctx.game:
            ctx.game.stop()                    # não deixa o PresentMon rodando sozinho
        if ctx.proctrack:
            ctx.proctrack.stop()
        if ctx.netmon:
            ctx.netmon.stop()
        srv.RequestHandlerClass.audit.flush()  # grava as últimas ações
        srv.server_close()


if __name__ == "__main__":
    main()
