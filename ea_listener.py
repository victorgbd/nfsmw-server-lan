#!/usr/bin/env python3
"""
Acts as an "EA server" on the LAN: listens and logs whatever the game sends.

    python ea_listener.py --ports 10600,10900-10910
    python ea_listener.py --ports 1-1024 --udp

It doesn't reply to anything (yet): it's for finding out which port the game
calls and with which protocol, which is the first thing you need to know to
imitate the server.

The game goes to the PC because the native engine, with --red_dns=<PC IP>,
answers that IP for any name it looks up (see the README). The port comes from
the game log: the line "[red] connect(..., IP:port)".

Whatever is received goes to the console and to logs/listener.log.

The first time, Windows asks whether to let Python accept connections: you have
to allow it on private networks, or the phone can't reach it.
"""
import argparse
import asyncio
import datetime
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
LOG = ROOT / "logs" / "listener.log"


def log(text):
    line = f"{datetime.datetime.now():%H:%M:%S.%f}"[:-3] + " " + text
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def hexdump(data, width=32):
    """Hex and text, in rows, like a hex editor."""
    rows = []
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hexa = " ".join(f"{b:02X}" for b in chunk)
        txt = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        rows.append(f"    {i:04X}  {hexa:<{width * 3}} |{txt}|")
    return "\n".join(rows)


def parse_ports(text):
    result = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            result.extend(range(int(a), int(b) + 1))
        else:
            result.append(int(part))
    return result


async def tcp_client(reader, writer, port):
    peer = writer.get_extra_info("peername")
    log(f"TCP {port}: connection from {peer[0]}:{peer[1]}")
    total = 0
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            total += len(data)
            log(f"TCP {port} <- {peer[0]}:{peer[1]} {len(data)} bytes\n{hexdump(data)}")
    except ConnectionError as e:
        log(f"TCP {port}: {e}")
    log(f"TCP {port}: {peer[0]}:{peer[1]} closed ({total} bytes in total)")
    writer.close()


class Udp(asyncio.DatagramProtocol):
    def __init__(self, port):
        self.port = port

    def datagram_received(self, data, peer):
        log(f"UDP {self.port} <- {peer[0]}:{peer[1]} {len(data)} bytes\n{hexdump(data)}")


async def run(ports, with_udp, ip):
    loop = asyncio.get_running_loop()
    opened, failed = [], []
    for p in ports:
        try:
            await asyncio.start_server(lambda r, w, p=p: tcp_client(r, w, p), ip, p)
            opened.append(f"tcp/{p}")
        except OSError:
            failed.append(f"tcp/{p}")
        if with_udp:
            try:
                await loop.create_datagram_endpoint(lambda p=p: Udp(p), local_addr=(ip, p))
                opened.append(f"udp/{p}")
            except OSError:
                failed.append(f"udp/{p}")
    summary = ", ".join(opened[:12]) + (" ..." if len(opened) > 12 else "")
    log(f"listening on {len(opened)} ports ({summary})")
    if failed:
        log(f"could not open {len(failed)} (in use or reserved): "
            + ", ".join(failed[:20]))
    await asyncio.Event().wait()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    ap.add_argument("--ports", required=True, help="list and ranges: 80,443,10000-10100")
    ap.add_argument("--udp", action="store_true", help="also listen on UDP on the same ports")
    ap.add_argument("--ip", default="0.0.0.0", help="interface (all of them by default)")
    a = ap.parse_args()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log("=" * 60)
    try:
        asyncio.run(run(parse_ports(a.ports), a.udp, a.ip))
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    sys.exit(main())
