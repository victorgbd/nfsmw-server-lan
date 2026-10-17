# nfsmw-servidor-lan

Minimal EA lobby server for playing **Need for Speed: Most Wanted (Xbox 360)** over a
local network with [NFSMW Recompiled](https://github.com/victorgbd/NFSMW-Recompiled-Mobile) (native engine, Android).

The 360 version has no System Link: its multiplayer only exists through "Xbox Live",
against EA's 2005 servers, which are long gone. This program stands in for those
servers on a PC on the same network. The game connects to it for the lobby (rooms,
games, player list), and then the phones race connected **directly to each other**
over UDP. The PC never sees the race traffic.


## Requirements

- Python 3.8 or newer. It only uses the standard library: nothing to install.
- The PC and the phones on the same network (same Wi-Fi, or wired to the same router).
- NFSMW Recompiled with the native engine on each phone.

## Usage

1. Find the PC's local network IP (`ipconfig` on Windows, `ip addr` on Linux), for
   example `10.0.0.2`.
2. Start the server with that IP:

   ```
   python ea_server.py --ip 10.0.0.2
   ```

   The first time, Windows asks whether to let Python accept incoming connections:
   allow it on **private networks**, or the phones won't be able to reach it.
3. On each phone, in the app settings, under "Servidor LAN (multijugador)" (LAN server
   (multiplayer)), enter the same IP. Each phone must have a **different gamertag**.
4. In the game: **Xbox Live → Quick Match**. One player creates a game and the others
   go into Quick Match and join it.

If the router gives the PC a different IP (DHCP), you have to change it in both places.
Giving the PC a fixed IP in the router avoids this.

### Options

| Option | What it does |
|---|---|
| `--ip <IP>` | The PC's IP on the LAN. Required: it's what the phones are given as the lobby server address. |
| `--log <file>` | Where to write the log. Defaults to `logs/server.log`, next to the script. |

The server listens on all interfaces, on TCP **30960** (directory) and **10901**
(lobby). The phones talk to each other over UDP **3658** (game) and **6000** (voice),
without going through the PC.

## What's here

| File | Purpose |
|---|---|
| `ea_server.py` | The server. Everything it receives and replies goes to the console and the log. |
| `ea_listener.py` | Diagnostic tool: listens on the ports you give it, logs whatever arrives and never replies. It was used to find out which ports the game calls. |

## The protocol

It's EA's 2003-2006 lobby protocol (DirtySock, "Aries"), with `PROD=nfs-xbox-2006`.
Each message has a 12-byte header:

```
4 bytes   command, as text: "@dir", "addr", "auth"...
4 bytes   subcode: 0 on success; on error, the error code as text
4 bytes   total message length, header included, big-endian
```

followed by `KEY=value` text separated by newlines, terminated by a 0.

- **Directory (30960):** the game sends `@dir` and gets back the IP and port of the
  lobby server.
- **Lobby (10901):** `addr`, `skey`, `news`, `auth`, `pers`, `sele` (plus the `+who`
  record), `user`, `usea`/`+uss` (room search), `ucre`/`ujoi`/`ulea`/`uadm`,
  `gcre`/`gjoi`/`gset`/`gsta`/`glea`/`gdel` (the game), `mesg`/`+msg`, `auxi`,
  `rank` (results) and a `~png` heartbeat every 20 s (without it the game drops back
  to the menu after 60 s).

The hard-won parts, in case they need changing:

- **The `ucre/ujoi/ulea/usea/uadm` "rooms" are *user sets*.** The game room's player
  list is the member list of the set in slot 0, and it is only filled by **`+usm`**:
  `S` (slot), `I`, `N`, `F`, `G` and `X`. `X` is the text each client sends with
  `auxi`, from which the game reads `SCF`, `LT`, `CD` and `CE` (connection, latency and
  race state). The first member must be yourself. Without this the room shows up empty
  ("Espera a que haya un número mínimo de jugadores" — "Waiting for a minimum number of
  players").
- The joining player subscribes with `sele USERSET0=<room>` before `ujoi`. The host
  sends nothing: it is given slot 0 when it creates the room.
- `+usr` is a different list (the lobby user list, where `X` is a different field). It
  doesn't count for the game room.
- The game record (`gcre`/`gjoi`/`+mgm`) carries, per player, `OPPO%d OPID%d ADDR%d
  LADDR%d MADDR%d OPPART%d OPPARAM%d OPFLAG%d PRES%d`. `MADDR` is the one the client
  sends in `auth`. `SESS` comes from the host in `gset` and must start with `$`.
- The user record (`+who`/`+usr`) needs `G` (the game the user is in; with 0 the game
  treats the match as lost) and `MA` (without it the link between players ends up with
  no XUID).

Unknown commands are answered with the same command and no data, and are logged as
`(unhandled command: replying empty)`: that's
the first thing to look for if something gets stuck.

The game side (sockets, XNet, engine hooks) lives in the NFSMW Recompiled repository,
in `docs/diario/red-y-privilegios.md`.

## Limitations

- A single LAN only. No NAT, no accounts, and nothing that persists state between
  runs: restarting the server makes the connected games lose their session, and you
  have to go back into Xbox Live.
- If a phone goes to the background during a race, Android kills it and the other
  player finishes alone.
- Leaderboards, friends and news are not implemented: they get empty replies.
