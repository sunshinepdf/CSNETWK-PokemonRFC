# PokeProtocol — Peer-to-Peer Pokémon Battle

A peer-to-peer Pokémon battle application built on a custom UDP-based protocol (PokeProtocol RFC). Two players can battle each other directly over a network, with support for spectators and in-battle chat.

## Features

- **Host or Join** battles over a local network or the internet
- **Broadcast discovery** — find open games automatically without knowing the host IP
- **P2P direct join** — connect to a host by IP and port
- **Spectator mode** — watch ongoing battles in real time
- **Turn-based battle system** — full attack, damage calculation, and faint detection
- **In-battle chat** — send text messages or sticker images during a battle
- **Reliable UDP messaging** — custom reliability layer with sequence numbers, ACKs, and retransmission

## Requirements

- Python 3.8 or higher
- No external dependencies (uses only the Python standard library)

## Project Structure

```
CSNETWK-PokemonRFC/
├── main.py               # Application entry point and CLI
├── state_machine.py      # Top-level state machine wrapper
├── pokemon.csv           # Pokémon stats database
├── clear_cache.py        # Utility to clear Python bytecode cache
└── protocol/
    ├── __init__.py
    ├── message.py        # RFC-style message serialization/parsing
    ├── udp_transport.py  # Raw UDP send/receive
    ├── reliability.py    # Reliability layer (seq numbers, ACKs, retransmission)
    ├── state_machine.py  # Protocol state machine (battle flow, message dispatch)
    ├── broadcast.py      # UDP broadcast game discovery
    ├── game_logic.py     # Damage calculation, type effectiveness, RNG sync
    ├── pokemon_database.py # Pokémon stats loader from CSV
    └── chat.py           # Chat message helpers
```

## Getting Started

### Run the application

```bash
python main.py
```

You will be presented with a menu:

```
============================================================
 POKEPROTOCOL - PEER-TO-PEER POKÉMON BATTLE
============================================================

Select mode:
  1. Host a game
  2. Join a game (Broadcast)
  3. Join a game (P2P)
  4. Spectate a game (Broadcast)
  0. Exit
```

### Host a game

1. Select **1** and enter your name.
2. Enter a port to host on (default: `5555`).
3. Your game will be announced over UDP broadcast so others can find it.
4. Wait for an opponent to connect, then use `setup` to choose your Pokémon.

### Join a game (Broadcast)

1. Select **2** and enter your name.
2. The application will scan the local network for open games.
3. Pick a game from the list to join.

### Join a game (P2P)

1. Select **3** and enter your name.
2. Enter the host IP address and port, plus your own local port (default: `5557`).

### Spectate a game

1. Select **4** and enter your name.
2. The application will discover available games via broadcast.
3. Pick a game — you will receive live battle updates and can send chat messages.

## In-Battle Commands

| Command | Description |
|---|---|
| `setup <pokemon> <sp_atk_uses> <sp_def_uses>` | Choose your Pokémon and allocate special move uses |
| `attack <move>` | Use a move on your turn |
| `chat <message>` | Send a text chat message to your opponent |
| `sticker <filepath>` | Send a sticker image file |
| `list` | Show your Pokémon's available moves and their stats |
| `status` | Display current battle status (HP, turn, state) |
| `quit` | Exit the application |

> Spectators can only use `chat`, `list`, and `status`.

## Protocol Overview

PokeProtocol uses plain-text UDP datagrams in an RFC key-value format:

```
message_type: BATTLE_SETUP
pokemon_name: Pikachu
sequence_number: 1
```

Key protocol messages include:

- `HANDSHAKE_REQUEST` / `HANDSHAKE_RESPONSE` — connection establishment and RNG seed sync
- `BATTLE_SETUP` — share chosen Pokémon stats with opponent
- `MOVE_ANNOUNCE` — declare the move used this turn
- `CALC_REPORT` — exchange damage calculation results
- `HP_UPDATE` — synchronize HP after each turn
- `GAME_OVER` — signal end of battle
- `CHAT_MESSAGE` — in-battle text or sticker message
- `ACK` — acknowledge a reliable message
- `SPECTATOR_REQUEST` / `SPECTATOR_UPDATE` — spectator join and state sync

The reliability layer assigns each message a monotonically increasing sequence number. The receiver sends an `ACK` for every sequenced message. Unacknowledged messages are retransmitted up to 3 times (500 ms timeout between attempts).

## Utility

To clear Python bytecode caches (useful after updating protocol modules):

```bash
python clear_cache.py
```

## Configuration

The broadcast discovery port defaults to `5556`. You can override it with an environment variable:

```bash
POKEMON_BROADCAST_PORT=6000 python main.py
```
