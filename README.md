# nfsmw-servidor-lan

Minimal EA lobby server for playing **Need for Speed: Most Wanted (Xbox 360)** over a
local network with [NFSMW Recompiled](https://github.com/victorgbd/NFSMW-Recompiled-Mobile) (native engine, Android).

The 360 version has no System Link: its multiplayer only exists through "Xbox Live",
against EA's 2005 servers, which are long gone. This program stands in for those
servers on a PC on the same network. The game connects to it for the lobby (rooms,
games, player list), and then the phones race connected **directly to each other**
over UDP. The PC never sees the race traffic.

## Reviving the 360 online with community servers

The bigger goal is to bring back the Xbox 360 version's online play. EA shut down
its servers years ago, and this project re-implements the protocol the game speaks
to them. Anyone can then run their own server, open to whoever they want to play
with: no EA, and no single server everything depends on.

The pieces the game needs are already here: the lobby, rooms, games, the player list,
gamertags, and stats and leaderboards (still in progress). For now it only works on a
LAN. Running a community server over the internet still needs NAT traversal (players
race peer to peer) and some kind of accounts (see [Limitations](#limitations)).
Contributions toward that are welcome.

## No PC? A phone can host

The app has this same server built in (a Java port, `ServidorEa.java` in the NFSMW
Recompiled repository): turn on **"Host on this phone"** under "LAN server" on one
phone, and the others enter the IP it shows. That phone races too. It works on a
Wi-Fi or over that phone's own hotspot. A protocol change here has to be made there
as well.

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
   (multiplayer)), press **Search the network** and tap the server, or add it by hand
   with the same IP. Each phone must have a **different gamertag**.
4. In the game: **Xbox Live → Quick Match**. One player creates a game and the others
   go into Quick Match and join it.

If the router gives the PC a different IP (DHCP), you have to change it in both places.
Giving the PC a fixed IP in the router avoids this.

### Options

| Option | What it does |
|---|---|
| `--ip <IP>` | The PC's IP on the LAN. Required: it's what the phones are given as the lobby server address. |
| `--log <file>` | Where to write the log. Defaults to `logs/server.log`, next to the script. |
| `--hold-minutes <n>` | How long a checked gamertag stays held for the phone that checked it (below). Default 10. |
| `--name <name>` | The name the app's server search shows (below). Defaults to the PC's name. |

The server listens on all interfaces, on TCP **30960** (directory), **10901**
(lobby) and **10902** (gamertag check, below), and on UDP **10902** (LAN search,
below). The phones talk to each other over UDP
**3658** (game) and **6000** (voice), without going through the PC.

### LAN search

The app's **Search the network** button sends one UDP datagram, `NFSMW_DISCOVER`, to
port **10902**: three times to the broadcast address of its Wi-Fi (or hotspot), and
once to every address of its /24, because some routers and phones drop broadcasts.
It listens for 1.5 s. Every server that hears it answers to the sender with

```
NFSMW_SERVER
NAME=<--name, or the PC's name>
PLAYERS=<players in Xbox Live>
```

and the app lists them; tapping one saves it and selects it. A phone that hosts
answers with its gamertag (or its model). The log shows one line per phone that
searches, every 10 s at most. If the search doesn't find a PC that's running, check
that Windows lets Python receive on private networks (UDP too).

### Gamertag check

Two players with the same gamertag break the lobby (rooms and games go by name), so
the app asks before letting you in. When you set a server, change your gamertag, open
the app, or press Play, it connects to TCP **10902** and sends one line,
`GAMERTAG <name>`; the server answers `FREE` or `IN_USE`.

- *In use* means **another device** (another IP) is connected to this server with
  that name, or holds it. Case doesn't matter, like Xbox Live. Your own device
  never counts against you.
- A `FREE` answer also **holds** the name for the phone that asked, for 10 minutes
  (`--hold-minutes`). That way two phones can't both be told a name is free and then
  meet in the lobby. A phone holds one name: checking another one releases the
  previous hold at once.
- While the app's main screen is open it checks again every minute, so the hold
  doesn't run out while you're there; pressing Play renews it, which leaves time
  to boot the game and reach Xbox Live, where the connection itself counts. When a
  player leaves Xbox Live, their name stays held for their phone for another 10
  minutes.
- Holds live in memory: restarting the server clears them.
- If it's in use, the app marks the gamertag field and won't start the game until
  you change the gamertag or the server. With no server set, nothing is checked.
- If the server doesn't answer (it's down, or an older version without this port),
  the app says so and lets you play.
- An older app that doesn't check can still log in with a name already in use: the
  server lets it in and logs a `warning: gamertag ... already in use`.

You can try it by hand with any TCP client, e.g. `ncat 10.0.0.2 10902` and type
`GAMERTAG victor`.

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

### Stats and leaderboards (work in progress)

Read from the game's code; kept in `data/stats.json` (and every race result as it
comes in `data/races.jsonl`), next to the script.

- **Personal stats** go in the reply to `user`: `STAT`, plus `LWSTAT` / `LMSTAT` (the
  same stats at the start of the week / month: the game subtracts them to show the
  progress) and `LGAME`. Each is 44 numbers in hex, comma-separated. 0–7 are general;
  then 12 per race mode, three modes: at 8+12m the points, then ranked wins, ranked
  losses, unranked wins, unranked losses.
- **Leaderboards** (and the Blacklist 15) are `snap` queries: `CHAN` (where the game
  stores the rows), `INDEX` (which leaderboard), `START`/`RANGE` or `FIND=$` (where am
  I). The reply repeats `CHAN` and says in `RANGE` how many rows follow, each a `+snp`
  with `N` name, `R` position, `P` points, `O` and `S` (7 numbers, same format: 1 and 2
  wins / losses, 6 points). `RANGE=0` means an empty leaderboard.
- **Race results** come in `rank` (`RESU`, URL-encoded base64) from each player when a
  race ends, with the whole race in it: byte 1 the number of players, byte 3 the index
  of the sender, then from byte 8 a block per player with its index and **position**
  (then floats: times, speeds...; 48 bytes, then a count and that many more bytes).
  Each player's own position goes to its stats, so a race doesn't count twice. The
  race mode and whether it's ranked come from the room (`M`, `P` in its parameters).
  Points are ours: (players − position + 1) × 10. Still unknown: which `M` is each
  block of `STAT` (only Circuit, `M=2`, is mapped) and what each `INDEX` is.

Unknown commands are answered with the same command and no data, and are logged as
`(unhandled command: replying empty)`: that's
the first thing to look for if something gets stuck.

The game side (sockets, XNet, engine hooks) lives in the NFSMW Recompiled repository,
in `docs/diario/red-y-privilegios.md`.

## Limitations

- A single LAN only. No NAT and no accounts. Only the stats persist between runs:
  restarting the server makes the connected games lose their session, and you have
  to go back into Xbox Live.
- If a phone goes to the background during a race, Android kills it and the other
  player finishes alone.
- Friends and news are not implemented: they get empty replies. Stats and
  leaderboards answer in the right format but stay at 0 (see above).
