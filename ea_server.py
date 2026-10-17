#!/usr/bin/env python3
"""
Minimal EA lobby server, for playing Most Wanted (360) over the LAN.

    python ea_server.py --ip 10.0.0.2

The game, with --red_lan=true --red_live=true --red_dns=<PC IP>, looks up
stestlobby03.beta.ea.com and connects here (see the README).

THE PROTOCOL
============
It's EA's 2003-2006 lobby protocol (DirtySock, "Aries"): every message has a
12-byte header

    4 bytes   command, as text: "@dir", "addr", "skey", "auth"...
    4 bytes   subcode: 0 on success; in an error reply, the error code as text
    4 bytes   total message length, header included, big-endian

followed by KEY=value text separated by newlines, terminated by a 0.

Two servers:
  - directory (30960): the game sends @dir and gets back the IP and port of the
    lobby server.
  - lobby (10901): everything else (addr, skey, news, auth, pers, rooms, games...).

Whatever isn't known yet is answered with the same command and no data, and gets
logged: the log (logs/server.log, or the one given with --log) is what tells you
what's missing.
"""
import argparse
import asyncio
import datetime
import pathlib
import random
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parent
LOG = ROOT / "logs" / "server.log"

DIR_PORT = 30960
LOBBY_PORT = 10901


def log(text):
    line = f"{datetime.datetime.now():%H:%M:%S.%f}"[:-3] + " " + text
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def pack_message(command, fields=None, sub=b"\0\0\0\0"):
    body = b""
    if fields:
        body = "".join(f"{k}={v}\n" for k, v in fields.items()).encode("latin-1")
    body += b"\0"
    return command + sub + struct.pack(">I", 12 + len(body)) + body


def parse_fields(body):
    text = body.rstrip(b"\0").decode("latin-1", "replace")
    fields = {}
    for line in text.split("\n"):
        if "=" in line:
            k, v = line.split("=", 1)
            fields[k] = v
    return fields


class Session:
    """What the server knows about a connected player."""

    def __init__(self, ip, writer=None):
        self.ip = ip
        self.name = "USER"
        self.persona = None
        self.room = None
        self.game = None
        self.writer = writer
        self.maddr = ""
        self.opparam = ""
        self.auxi = ""
        # User sets it is subscribed to: room name -> slot (0..4).
        self.sets = {}
        Session.count = getattr(Session, "count", 0) + 1
        self.id = Session.count


class Server:
    def __init__(self, ip):
        self.ip = ip
        self.rooms = {}
        self.games = {}
        self.sessions = set()
        self.next_id = 1000

    def forget(self, ses):
        """The player left: their room disappears and they leave their game."""
        self.leave_game(ses)
        if ses.room in self.rooms:
            self.notify_set(self.rooms[ses.room], ses, ses, remove=True)  # their own copy goes nowhere
        if ses.room in self.rooms and self.rooms[ses.room]["O"] == (ses.persona or ses.name):
            log(f"   room {ses.room} of {self.rooms[ses.room]['O']} deleted")
            del self.rooms[ses.room]

    # -- directory --------------------------------------------------------
    def dir_(self, ses, fields):
        return [pack_message(b"@dir", {
            "ADDR": self.ip,
            "PORT": LOBBY_PORT,
            "SESS": random.randint(1000000000, 1999999999),
            "MASK": "%032x" % random.getrandbits(128),
        })]

    def tic(self, ses, fields):
        # "@tic RC4+MD5-V2": the client offers to encrypt the session. With no
        # reply, it stays in plain text (it sends @dir without waiting).
        return []

    # -- lobby -------------------------------------------------------------
    def addr(self, ses, fields):
        return []  # the client reports its IP and port; no reply

    def skey(self, ses, fields):
        return [pack_message(b"skey", {"SKEY": "$51ba8aee64ddfacae5baefa6bf61e009"})]

    def news(self, ses, fields):
        return [pack_message(b"news", {
            "BUDDY_SERVER": self.ip,
            "BUDDY_PORT": 10899,
            "TOSAC_URL": "",
            "TOSA_URL": "",
            "TOS_URL": "",
        })]

    def sele(self, ses, fields):
        # It gets back the same thing it asks for ("yes, all of that") and, after
        # that, its own record (+who): EA's servers sent it on login.
        reply = [pack_message(b"sele", fields), self.user_record(ses)]
        # USERSETk=<room>: subscribes in slot k to the members of that room
        # (the joining player sends it before ujoi). It is sent the room (+ust)
        # and every member the room already has (+usm).
        for key, value in fields.items():
            if not (key.startswith("USERSET") and key[7:].isdigit()):
                continue
            slot = int(key[7:])
            ses.sets = {n: k for n, k in ses.sets.items() if k != slot}
            if not value:
                continue
            ses.sets[value] = slot
            room = self.room_by_name(value)
            if room:
                reply.append(pack_message(b"+ust", self.room_record(room)))
                for m in self.members(room):
                    reply.append(self.member_record(m, slot))
        return reply

    # -- user sets (the "rooms" of ucre/ujoi/ulea/usea) ------------------------
    # The game room (LobbyGameSessions, list +360) is the member list of the
    # user set in slot 0 (lobby list 24). It is filled with '+usm'
    # (sub_8282EB78): S slot, I, N, F, G and X (160-byte record,
    # sub_82826530: +4 name, +28 auxi text with SCF/LT/CD/CE). Without N, it
    # removes member I. Member 0 of the game room is the one with your own
    # name (sub_82544C28): without your own entry the list comes out empty and
    # never reaches the minimum number of players.

    def room_by_name(self, name):
        for room in self.rooms.values():
            if room["N"] == name:
                return room
        return None

    def members(self, room):
        return [o for o in self.sessions if o.room == room["I"]]

    def member_record(self, ses, slot, remove=False):
        if remove:
            return pack_message(b"+usm", {"S": slot, "I": ses.id})
        return pack_message(b"+usm", {
            "S": slot,
            "I": ses.id,
            "N": ses.persona or ses.name,
            "F": "U",
            "G": ses.game["IDENT"] if ses.game else 0,
            "X": ses.auxi,
        })

    def notify_set(self, room, ses, requester, remove=False):
        """'+usm' for `ses` to the subscribers of `room`; `requester`'s copy is returned."""
        own = []
        for o in self.sessions:
            slot = o.sets.get(room["N"])
            if slot is None or not o.writer:
                continue
            msg = self.member_record(ses, slot, remove)
            if o is requester:
                own.append(msg)
            else:
                o.writer.write(msg)
        return own

    def user(self, ses, fields):
        # Asks for a profile's record (on login, its own).
        return [pack_message(b"user", {
            "PERS": fields.get("PERS", ses.persona or ses.name),
            "LAST": "2005.11.15-00:00:00",
            "EXPR": "1072566000",
            "STAT": "",
        })]

    def usea(self, ses, fields):
        # Player room search (the Quick Match one, with CUSTFLAGS/CUSTMASK).
        # The reply only says how many (COUNT); each one arrives afterwards in a
        # '+uss' with its record: the game's message table (0x828F2264) sends
        # '+uss' to sub_8282EF18, which puts it in the results list.
        others = [s for s in self.rooms.values() if s["O"] != (ses.persona or ses.name)]
        r = [pack_message(b"usea", {"START": fields.get("START", 0), "COUNT": len(others)})]
        r += [pack_message(b"+uss", self.room_record(s)) for s in others]
        return r

    # -- games ---------------------------------------------------------------
    # A game is the race inside a room. The record the game reads
    # (sub_82825C90): IDENT NAME SELF HOST PARAMS PLATPARAMS ROOM CUSTFLAGS
    # SYSFLAGS COUNT PRIV MINSIZE MAXSIZE NUMPART SEED WHEN AUTH SESS, and for
    # each player i (strings from the game): OPPO OPID ADDR LADDR MADDR OPPART
    # OPPARAM OPFLAG PRES, with PARTSIZE0/PARTPARAMS0 for the single "part".
    # Notices to the others: '+mgm' (your game has changed) and '+ses' (it starts).

    def game_record(self, p, viewer):
        f = {
            "IDENT": p["IDENT"],
            "NAME": p["NAME"],
            "SELF": viewer.persona or viewer.name,
            "HOST": p["HOST"],
            "PARAMS": p["PARAMS"],
            "PLATPARAMS": p["PLATPARAMS"],
            "ROOM": p["ROOM"],
            "CUSTFLAGS": p["CUSTFLAGS"],
            "SYSFLAGS": p["SYSFLAGS"],
            "COUNT": len(p["players"]),
            "PRIV": 0,
            "MINSIZE": p["MINSIZE"],
            "MAXSIZE": p["MAXSIZE"],
            "NUMPART": 1,
            "PARTSIZE0": p["MAXSIZE"],
            "PARTPARAMS0": "",
            "SEED": p["SEED"],
            "WHEN": p["WHEN"],
            "AUTH": "",
            "SESS": p.get("SESS", ""),
        }
        for i, j in enumerate(p["players"]):
            f[f"OPPO{i}"] = j.persona or j.name
            f[f"OPID{i}"] = j.id
            f[f"ADDR{i}"] = j.ip
            f[f"LADDR{i}"] = j.ip
            f[f"MADDR{i}"] = j.maddr
            f[f"OPPART{i}"] = 0
            f[f"OPPARAM{i}"] = j.opparam
            f[f"OPFLAG{i}"] = 0
            f[f"PRES{i}"] = 0
        return f

    def notify(self, p, command=b"+mgm", skip=None):
        """Sends the game record to all of its players (except one)."""
        for j in p["players"]:
            if j is skip or not j.writer:
                continue
            msg = pack_message(command, self.game_record(p, j))
            log(f"   {command.decode()} -> {j.persona or j.name} (COUNT={len(p['players'])})")
            j.writer.write(msg)

    def gcre(self, ses, fields):
        me = ses.persona or ses.name
        self.next_id += 1
        p = {
            "IDENT": self.next_id,
            "NAME": fields.get("NAME", me),
            "HOST": me,
            "PARAMS": fields.get("PARAMS", ""),
            "PLATPARAMS": fields.get("PLATPARAMS", ""),
            "ROOM": ses.room or 0,
            "CUSTFLAGS": fields.get("CUSTFLAGS", 0),
            "SYSFLAGS": fields.get("SYSFLAGS", 0),
            "MINSIZE": fields.get("MINSIZE", 2),
            "MAXSIZE": fields.get("MAXSIZE", 4),
            "SEED": random.randint(1, 2**31 - 1),
            "WHEN": datetime.datetime.now().strftime("%Y.%m.%d-%H:%M:%S"),
            "players": [ses],
        }
        self.games[p["NAME"]] = p
        ses.game = p
        # Then, its record with G = this game (see user_record()).
        return [pack_message(b"gcre", self.game_record(p, ses)), self.user_record(ses)]

    def gjoi(self, ses, fields):
        p = self.games.get(fields.get("NAME", ""))
        if not p:
            log(f"   gjoi: no game '{fields.get('NAME')}'")
            return [pack_message(b"gjoi", {}, sub=b"nfnd")]
        if ses not in p["players"]:
            p["players"].append(ses)
        ses.game = p
        self.notify(p, skip=ses)
        # Only the record: the joining player connects to the host with this
        # record's SESS (sub_82542400). '+ses' is for when the host starts (gsta):
        # if it arrives earlier, it deletes its Xbox session.
        return [pack_message(b"gjoi", self.game_record(p, ses)), self.user_record(ses)]

    def gset(self, ses, fields):
        p = ses.game
        if not p:
            return [pack_message(b"gset", {})]
        # Everything the host sends is stored. Watch out for SESS: it's its
        # encoded Xbox session ("$..."); the joining player only connects if its
        # record carries a SESS starting with '$' (sub_82542400 -> sub_82547838).
        for key, value in fields.items():
            if key != "NAME":
                p[key] = value
        self.notify(p, skip=ses)
        return [pack_message(b"gset", self.game_record(p, ses))]

    def leave_game(self, ses):
        p = getattr(ses, "game", None)
        if not p:
            return
        if ses in p["players"]:
            p["players"].remove(ses)
        ses.game = None
        if not p["players"] or p["HOST"] == (ses.persona or ses.name):
            self.games.pop(p["NAME"], None)
            for j in p["players"]:
                j.game = None
            log(f"   game {p['NAME']} closed")
        else:
            self.notify(p)

    def glea(self, ses, fields):
        self.leave_game(ses)
        return [pack_message(b"glea", {})]

    def gdel(self, ses, fields):
        self.leave_game(ses)
        return [pack_message(b"gdel", {})]

    def gsta(self, ses, fields):
        # The host starts: '+ses' with the record (and its SESS) to everyone.
        p = ses.game
        if not p:
            return [pack_message(b"gsta", {})]
        self.notify(p, command=b"+ses")
        return [pack_message(b"gsta", {})]

    def find_room(self, fields):
        for key in ("I", "IDENT", "ROOM"):
            if fields.get(key, "").lstrip("-").isdigit():
                room = self.rooms.get(int(fields[key]))
                if room:
                    return room
        name = fields.get("NAME") or fields.get("N")
        for room in self.rooms.values():
            if room["N"] == name or room["O"] == name:
                return room
        return None

    def ujoi(self, ses, fields):
        # Join someone else's room (the one found with usea). The request format
        # is still to be confirmed: the room is looked up by id or by name.
        room = self.find_room(fields)
        if not room:
            log(f"   ujoi: can't find the room for {fields}")
            return [pack_message(b"ujoi", {}, sub=b"nfnd")]
        room["C"] = int(room["C"]) + 1
        ses.room = room["I"]
        # Those already in the room get the joining player's record ('+usr') and
        # the joining player gets each of theirs. With that record (LA, MA) the
        # host registers the newcomer in its peer-to-peer connection
        # (sub_82547C50 -> ConnApiAddClient); without it, it doesn't open its
        # ports and doesn't answer the handshake.
        reply = [pack_message(b"ujoi", self.room_record(room))]
        for other in self.sessions:
            if other is not ses and other.room == room["I"] and other.writer:
                log(f"   +usr {ses.persona or ses.name} -> {other.persona or other.name}")
                other.writer.write(self.user_record(ses, b"+usr"))
                reply.append(self.user_record(other, b"+usr"))
        reply.append(self.user_record(ses, b"+usr"))
        reply += self.notify_set(room, ses, ses)
        return reply

    def ulea(self, ses, fields):
        room = self.rooms.get(ses.room)
        if room and int(room["C"]) > 1:
            room["C"] = int(room["C"]) - 1
        ses.room = None
        reply = [pack_message(b"ulea", {})]
        if room:
            reply += self.notify_set(room, ses, ses, remove=True)
        return reply

    def mesg(self, ses, fields):
        # Message to the game. On joining, the joining player sends TEXT=42
        # ATTR=EGS: the "I'm here" that makes the host register it in the
        # peer-to-peer connection (sub_822787A0: looks up its +usr record by name
        # and calls ConnApiAddClient). The others get it as '+msg': N sender,
        # T text, F flags (the one-letter key from 0x820A152C).
        p = ses.game
        targets = [j for j in (p["players"] if p else []) if j is not ses and j.writer]
        if not targets and ses.room is not None:
            targets = [o for o in self.sessions
                       if o is not ses and o.room == ses.room and o.writer]
        for j in targets:
            log(f"   +msg {ses.persona or ses.name} -> {j.persona or j.name}: "
                f"{fields.get('TEXT')} {fields.get('ATTR')}")
            j.writer.write(pack_message(b"+msg", {
                "N": ses.persona or ses.name,
                "T": fields.get("TEXT", ""),
                "F": fields.get("ATTR", ""),
            }))
        return [pack_message(b"mesg", {})]

    def uadm(self, ses, fields):
        # The host changes its room (parameters, flags): it is stored and the
        # reply carries the new record.
        room = self.rooms.get(ses.room)
        if room:
            room["N"] = fields.get("NAME", room["N"])
            room["D"] = fields.get("DESC", room["D"])
            room["P"] = fields.get("PARAMS", room["P"])
            room["CF"] = fields.get("CUSTFLAGS", room["CF"])
            return [pack_message(b"uadm", self.room_record(room))]
        return [pack_message(b"uadm", {})]

    def room_record(self, room):
        # The keys the game reads from the reply (sub_82825B28, in order):
        # I ident, T type, SF/CF flags, O owner (if empty the game doesn't go on
        # with gcre), S seats, C taken, N name, D description, P parameters.
        return {
            "I": room["I"],
            "T": room["T"],
            "SF": room["SF"],
            "CF": room["CF"],
            "O": room["O"],
            "S": room["S"],
            "C": room["C"],
            "N": room["N"],
            "D": room["D"],
            "P": room["P"],
        }

    def ucre(self, ses, fields):
        # Create a player room ("Crear partida" — "Create game" — in Quick Match).
        # Afterwards, the game sends gcre to create the game inside it.
        self.next_id += 1
        room = {
            "I": self.next_id,
            "T": fields.get("TYPE", 0),
            "SF": fields.get("SYSFLAGS", ""),
            "CF": fields.get("CUSTFLAGS", ""),
            "O": ses.persona or ses.name,
            "S": fields.get("SIZE", 4),
            "C": 1,
            "N": fields.get("NAME", ""),
            "D": "",
            "P": fields.get("PARAMS", ""),
        }
        self.rooms[room["I"]] = room
        ses.room = room["I"]
        # The owner is a member of its room and doesn't send 'sele USERSET0': it is
        # given slot 0 (the game room's) and its own entry.
        ses.sets = {n: k for n, k in ses.sets.items() if k != 0}
        ses.sets[room["N"]] = 0
        return [pack_message(b"ucre", self.room_record(room)), self.user_record(ses, b"+usr"),
                self.member_record(ses, 0)]

    def auxi(self, ses, fields):
        # The player's auxiliary text (its state in the room); it is stored, echoed
        # back and, since it changes the player's record, a new '+usr' goes to
        # everyone in its room, the player included.
        ses.auxi = fields.get("TEXT", "")
        reply = [pack_message(b"auxi", fields)]
        if ses.room is not None:
            for other in self.sessions:
                if other is not ses and other.room == ses.room and other.writer:
                    other.writer.write(self.user_record(ses, b"+usr"))
            reply.append(self.user_record(ses, b"+usr"))
            room = self.rooms.get(ses.room)
            if room:
                reply += self.notify_set(room, ses, ses)
        return reply

    def user_record(self, ses, command=b"+who"):
        # The player's own record. The game uses it to build its entry in the
        # peer-to-peer connection (sub_82548818): LA (local IP) and MA (the MADDR
        # it sent in auth: "$<XUID>^<encoded XNADDR>"). Without MA, ConnApi is
        # left with XUID 0 and ConnApiOnline fails.
        return pack_message(command, {
            "I": ses.id,
            "M": ses.name,
            "N": ses.persona or ses.name,
            "MA": ses.maddr,
            "A": ses.ip,
            "LA": ses.ip,
            # G: the game the player is in (its IDENT; 0 = none). The game reads it
            # from the record (+304, one-letter key at 0x8202AA18) when it gets
            # +mgm: with 0 it treats the game as lost and tears it down
            # (sub_82542560).
            "G": ses.game["IDENT"] if ses.game else 0,
            "P": 1,
            "F": "U",
            "LO": "esES",
            "PRES": 1,
            # X: its auxi text. The game stores each +usr record in the room list
            # (LobbyGameSessions, +360) and reads from that text (record +28) SCF,
            # LT, CD and CE: connection state, latency and race state.
            "X": ses.auxi,
        })

    def auth(self, ses, fields):
        ses.name = fields.get("NAME") or fields.get("GTAG") or ses.name
        ses.maddr = fields.get("MADDR", ses.maddr)
        return [pack_message(b"auth", {
            "NAME": ses.name,
            "ADDR": ses.ip,
            "PERSONAS": ses.name,
            "LOC": "esES",
            "MAIL": "lan@lan",
            "SPAM": "NN",
            "TOS": 1,
            "SINCE": "2005.11.15-00:00:00",
            "LAST": "2005.11.15-00:00:00",
        })]

    def pers(self, ses, fields):
        ses.persona = fields.get("PERS") or fields.get("GTAG") or ses.name
        ses.maddr = fields.get("MADDR", ses.maddr)
        return [pack_message(b"pers", {
            "PERS": ses.persona,
            "LKEY": "%032x" % random.getrandbits(128),
            "LOC": "esES",
            "A": ses.ip,
            "LA": ses.ip,
            "IDLE": 600000,
        })]

    def png(self, ses, fields):
        # The client's answer to our ping: no reply (it would be a ping loop).
        return []

    def respond(self, ses, command, fields):
        name = command.decode("latin-1").lstrip("@~").replace(" ", "_")
        method = getattr(self, name + "_", None) or getattr(self, name, None)
        if method:
            return method(ses, fields), True
        return [pack_message(command)], False


async def heartbeat(writer, port):
    """The lobby server sends ~png every so often; with no traffic, the client
    considers the connection dead after 60 s and goes back to the main menu."""
    try:
        while True:
            await asyncio.sleep(20)
            ref = datetime.datetime.now().strftime("%Y.%m.%d-%H:%M:%S")
            writer.write(pack_message(b"~png", {"REF": ref, "TIME": 2}))
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass


async def handle_client(reader, writer, server, port):
    peer = writer.get_extra_info("peername")
    ses = Session(peer[0], writer)
    server.sessions.add(ses)
    log(f"[{port}] connection from {peer[0]}:{peer[1]}")
    buffer = b""
    heartbeat_task = asyncio.create_task(heartbeat(writer, port)) if port == LOBBY_PORT else None
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            buffer += data
            while len(buffer) >= 12:
                length = struct.unpack(">I", buffer[8:12])[0]
                if length < 12 or length > 1 << 20:
                    log(f"[{port}] impossible length {length}: {buffer[:32].hex()}")
                    buffer = b""
                    break
                if len(buffer) < length:
                    break
                msg, buffer = buffer[:length], buffer[length:]
                command, sub, body = msg[:4], msg[4:8], msg[12:]
                fields = parse_fields(body)
                log(f"[{port}] <- {command.decode('latin-1')} sub={sub.hex()} {fields}")
                replies, handled = server.respond(ses, command, fields)
                if not handled:
                    log(f"[{port}]    (unhandled command: replying empty)")
                for r in replies:
                    log(f"[{port}] -> {r[:4].decode('latin-1')} {parse_fields(r[12:])}")
                    writer.write(r)
                await writer.drain()
    except ConnectionError as e:
        log(f"[{port}] {e}")
    if heartbeat_task:
        heartbeat_task.cancel()
    server.forget(ses)
    server.sessions.discard(ses)
    log(f"[{port}] {peer[0]}:{peer[1]} closed")
    writer.close()


async def run(ip):
    server = Server(ip)
    for p in (DIR_PORT, LOBBY_PORT):
        await asyncio.start_server(lambda r, w, p=p: handle_client(r, w, server, p), "0.0.0.0", p)
    log(f"EA server on {ip}: directory {DIR_PORT}, lobby {LOBBY_PORT}")
    await asyncio.Event().wait()


def main():
    global LOG
    ap = argparse.ArgumentParser(description="Minimal EA lobby server for the LAN.")
    ap.add_argument("--ip", required=True, help="this PC's IP on the LAN (the one given in red_dns)")
    ap.add_argument("--log", help=f"log file (default {LOG})")
    a = ap.parse_args()
    if a.log:
        LOG = pathlib.Path(a.log).resolve()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log("=" * 60)
    try:
        asyncio.run(run(a.ip))
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    sys.exit(main())
