from __future__ import annotations

import argparse
import logging

from .config import load
from .service import BBS
from .store import Store
from .transports.kiss import KissTransport
from .transports.udp import UdpTransport


def build_transport(cfg, tcfg):
    kind = tcfg.get("type", "kiss")
    if kind == "kiss":
        return KissTransport(tcfg["host"], int(tcfg.get("port", 8001)), cfg.call, cfg.own_node_call)
    if kind == "udp":
        if cfg.node_call and cfg.node_call.upper() != cfg.call.upper():
            raise SystemExit("ext-UDP nie pozwala wybrac znaku nadawcy: BBS nadaje pod znakiem noda, "
                             "wiec call musi byc rowny znakowi noda (inny SSID wymaga KISS)")
        port = int(tcfg.get("port", 1799))   # MeshCom: ten sam port w obu kierunkach (domyslnie 1799)
        return UdpTransport(tcfg["host"], port, port, cfg.own_node_call,
                            tuple(tcfg.get("ack_statuses", [2])))
    raise SystemExit("nieznany transport: %r (kiss albo udp)" % kind)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="mcbbs", description="BBS dla sieci MeshCom")
    ap.add_argument("-c", "--config", required=True, help="plik konfiguracji TOML")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--log", default="", metavar="PLIK", help="zapisuj log tez do pliku (rotacja 1 MB x 5)")
    ap.add_argument("--shell", action="store_true", help="lokalny shell administracyjny (bez laczenia z nodem)")
    ap.add_argument("--as", dest="as_call", default="", help="znak, jako ktory dziala shell (domyslnie znak bazowy BBS)")
    args = ap.parse_args(argv)
    handlers = [logging.StreamHandler()]
    if args.log:
        from logging.handlers import RotatingFileHandler
        handlers.append(RotatingFileHandler(args.log, maxBytes=1_000_000, backupCount=5, encoding="utf-8"))
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg, tcfg = load(args.config)
    if args.shell:
        from .shell import run_shell
        run_shell(cfg, Store(cfg.db, reset_waiting=False), args.as_call)
        return
    store = Store(cfg.db)
    bbs = BBS(cfg, store, build_transport(cfg, tcfg))
    gateway = None
    if cfg.js8.get("host"):
        from .gateways.js8call import Js8Gateway
        gateway = Js8Gateway(cfg, store, urgent_cb=bbs.urgent_notice,
                             forward_cb=lambda g, t, now: bbs.send_group(g, t, now))
        gateway.start()
    try:
        bbs.run_forever()
    finally:
        if gateway:
            gateway.stop()


if __name__ == "__main__":
    main()
