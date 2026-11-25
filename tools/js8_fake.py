#!/usr/bin/env python3
"""Atrapa API JS8Call do testow bramki BBS bez radia i bez drugiej stacji.

Udaje JS8Call: slucha na porcie, a gdy BBS sie polaczy, odpowiada na STATION.GET_CALLSIGN i wysyla wiadomosci
RX.DIRECTED podane w linii polecen (w ksztalcie z dokumentacji API).

Przyklad (BBS: [js8call] host="127.0.0.1", port=2443, groups=["@GRM"], urgent=["@EMC"], private=["@PM"]):
  python3 tools/js8_fake.py --port 2443 \
      "SP9XYZ>@GRM>Zalew : woda w centrum" \
      "SP1GHK>@EMC>Pilny kontakt : potrzebna lacznosc" \
      "SP9XYZ>@PM>SQ9GIN Temat : zadzwon"
Format argumentu: NADAWCA>ADRESAT>TEKST.
"""
import argparse
import json
import socket
import time


def directed(frm, to, text):
    return {"params": {"CMD": "", "DIAL": 7078000, "FREQ": 7078816, "FROM": frm, "GRID": "", "OFFSET": 816,
                       "SNR": 1, "SPEED": 0, "TO": to, "UTC": int(time.time() * 1000), "_ID": -1},
            "type": "RX.DIRECTED", "value": "%s: %s %s ♢ " % (frm, to, text)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=2443)
    ap.add_argument("--station", default="SP2ONG", help="znak stacji JS8Call zwracany przez STATION.GET_CALLSIGN")
    ap.add_argument("msgs", nargs="+", help="NADAWCA>ADRESAT>TEKST")
    args = ap.parse_args()
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", args.port))
    srv.listen(1)
    print("Czekam na BBS na 127.0.0.1:%d ..." % args.port)
    conn, _ = srv.accept()
    conn.settimeout(2)
    try:                                               # BBS pyta o znak stacji
        print("BBS:", conn.recv(4096).decode(errors="replace").strip())
    except socket.timeout:
        pass
    conn.sendall((json.dumps({"params": {"_ID": 1}, "type": "STATION.CALLSIGN", "value": args.station}) + "\n").encode())
    for m in args.msgs:
        frm, to, text = m.split(">", 2)
        conn.sendall((json.dumps(directed(frm.upper(), to.upper(), text), ensure_ascii=False) + "\n").encode())
        print("wyslano:", frm.upper(), "->", to.upper(), text)
        time.sleep(1)
    time.sleep(1)
    conn.close()


if __name__ == "__main__":
    main()
