"""
protocol/state_machine.py (fixed)

Protocol State Machine for PokeProtocol RFC
This module implements the state machine that manages the battle flow,
message handling, and chat functionality according to the PokeProtocol RFC.
It works WITH the reliability layer and transport, not independently.

This module:
- Manages battle state transitions
- Handles incoming messages and dispatches to appropriate handlers
- Sends messages through the reliability layer
- Integrates chat functionality
- Processes battle logic events
"""

import random
from typing import Dict, Tuple, Optional, Any, List
from .message import (
    decode_message,
    encode_message,
    require_fields,
    parse_int_field,
)
from .reliability import ReliabilityLayer, ReliabilityError
from .game_logic import BattlePokemon, calculate_damage, Move
from . import chat


class ProtocolStateMachine:
    # ----------------------------
    # Constructor
    # ----------------------------
    def __init__(self, transport, reliability: ReliabilityLayer, role: str, local_name: str = "Player"):
        """
        role: "HOST", "JOINER", or "SPECTATOR"
        """
        self.transport = transport
        self.r = reliability
        self.role = role
        self.local_name: str = local_name
        self.remote_name: Optional[str] = None
        # Spectator naming and last-incoming tracking
        self.spectator_names: Dict[Tuple[str, int], str] = {}
        self.last_incoming_addr: Optional[Tuple[str, int]] = None

        # Remote peer address (filled after handshake)
        self.peer_addr: Optional[Tuple[str, int]] = None

        # Local battle Pokémon and remote Pokémon
        self.local_pokemon: Optional[BattlePokemon] = None
        self.remote_pokemon: Optional[BattlePokemon] = None

        # Whose turn? ("LOCAL" or "REMOTE")
        self.turn_owner: Optional[str] = None

        # Last move from remote player
        self.remote_move: Optional[str] = None

        # Last move we announced (so attacker can re-use it later)
        self.last_announced_move: Optional[str] = None

        # Storage for last received calculation report
        self.last_calc_report_remote: Optional[Dict[str, Any]] = None
        self.local_calc_report: Optional[Dict[str, Any]] = None

        # Spectator list (for HOST)
        self.spectators: List[Tuple[str, int]] = []

        # Game state flags
        self.state = "SETUP"
        self.running = True

    # ----------------------------
    # Helper for sending messages
    # ----------------------------
    def _send_reliable(self, fields: Dict[str, Any]):
        if not self.peer_addr:
            return False, None
        ok, seq = self.r.send_reliable(self.transport, fields, self.peer_addr)
        # If we're HOST, also relay to all spectators so they receive events
        if self.role == "HOST" and self.spectators:
            for spec_addr in list(self.spectators):
                try:
                    self.r.send_reliable(self.transport, dict(fields), spec_addr)
                except Exception:
                    pass
        return ok, seq
    
    def _print_message(self, fields: Dict[str, Any], seq: Optional[int] = None):
        """Print an outgoing message in RFC wire format with sequence_number if available."""
        print(f"\n[{self.local_name}]")
        for key, value in fields.items():
            # Avoid double-printing sequence_number and avoid dumping full Pokémon stats
            if key == "sequence_number":
                continue
            if key == "pokemon":
                print("pokemon: [sent]")
                continue
            print(f"{key}: {value}")
        if seq is not None:
            print(f"sequence_number: {seq}")

    def _print_incoming_header(self):
        label = self.remote_name or "REMOTE"
        addr = self.last_incoming_addr
        if self.role == "HOST" and addr is not None:
            if self.peer_addr and addr == self.peer_addr:
                label = self.remote_name or f"REMOTE {addr[0]}:{addr[1]}"
            else:
                spec = self.spectator_names.get(addr)
                if not spec:
                    spec = f"{addr[0]}:{addr[1]}"
                label = f"SPECTATOR {spec}"
        print(f"\n[{label}]")

    # ----------------------------
    # Tick called every loop
    # ----------------------------
    def tick(self):
        """
        Called from the main loop. Runs retransmission timers.
        Also triggers local calculation report if both sides are processing the turn.
        """
        try:
            self.r.tick(self.transport)
        except ReliabilityError:
            print("[StateMachine] Peer unresponsive. Ending battle.")
            self.running = False
            return

        # Spectators never calculate or send reports
        if self.role == "SPECTATOR":
            return

        # If both sides should be processing the turn and we haven't sent our local
        # calculation report yet, attempt to send it.
        if self.state == "PROCESSING_TURN" and self.local_calc_report is None:
            # Determine which move to use: if we're the attacker, use last_announced_move;
            # otherwise use remote_move (defender sees remote_move).
            move = None
            if self.turn_owner == "LOCAL":
                move = self.last_announced_move
            else:
                move = self.remote_move

            if move:
                # Send calculation report (this will also update HP locally)
                try:
                    self.send_calculation_report(move)
                except Exception as e:
                    print(f"[StateMachine] Error sending calculation report: {e}")

    # ----------------------------
    # Incoming dispatcher
    # ----------------------------
    def handle_incoming(self, incoming: Tuple[bytes, Tuple[str, int]]):
        data, addr = incoming
        msg = decode_message(data)
        message_type = msg.get("message_type")
        # Track incoming address for header context
        self.last_incoming_addr = addr

        # Save peer address BEFORE processing reliability
        if self.peer_addr is None and self.role != "SPECTATOR":
            self.peer_addr = addr

        self.r.incoming_message(msg, addr, self.transport)

        # If HOST receives a battle/control event from JOINER, relay it to spectators
        if self.role == "HOST" and self.peer_addr and addr == self.peer_addr:
            # Relay everything except ACKs to spectators so they see the same stream
            if message_type and message_type != "ACK":
                forward_fields = dict(msg)
                # strip reliability-only fields when forwarding
                forward_fields.pop("sequence_number", None)
                forward_fields.pop("ack_number", None)
                for spec_addr in list(self.spectators):
                    try:
                        self.r.send_reliable(self.transport, dict(forward_fields), spec_addr)
                    except Exception:
                        pass

        # Dispatch by message type
        if message_type == "HANDSHAKE_REQUEST":
            self._on_handshake_request(msg, addr)

        elif message_type == "HANDSHAKE_RESPONSE":
            self._on_handshake_response(msg)

        elif message_type == "SPECTATOR_REQUEST":
            self._on_spectator_request(msg, addr)

        elif message_type == "BATTLE_SETUP":
            self._on_battle_setup(msg)

        elif message_type == "ATTACK_ANNOUNCE":
            self._on_attack_announce(msg)

        elif message_type == "DEFENSE_ANNOUNCE":
            self._on_defense_announce(msg)

        elif message_type == "CALCULATION_REPORT":
            self._on_calculation_report(msg)

        elif message_type == "CALCULATION_CONFIRM":
            self._on_calculation_confirm(msg)

        elif message_type == "RESOLUTION_REQUEST":
            self._on_resolution_request(msg)

        elif message_type == "GAME_OVER":
            self._on_game_over(msg)

        elif message_type == "CHAT_MESSAGE":
            self._on_chat(msg)
            # Relay chat across roles:
            # - If HOST receives from spectator (addr != peer_addr), forward to JOINER
            # - If HOST receives from JOINER (addr == peer_addr), forward to spectators
            if self.role == "HOST":
                forward_fields = dict(msg)
                # Remove reliability-only fields when forwarding
                forward_fields.pop("sequence_number", None)
                forward_fields.pop("ack_number", None)
                if self.peer_addr and addr != self.peer_addr:
                    # from spectator -> forward to joiner
                    try:
                        self.r.send_reliable(self.transport, forward_fields, self.peer_addr)
                    except Exception:
                        pass
                else:
                    # from joiner -> forward to all spectators
                    for spec_addr in list(self.spectators):
                        try:
                            self.r.send_reliable(self.transport, dict(forward_fields), spec_addr)
                        except Exception:
                            pass

        elif message_type == "ACK":
            # Handle ACK for reliability layer
            ack_num = msg.get("ack_number")
            if ack_num:
                try:
                    self.r.handle_ack(int(ack_num))
                except ValueError:
                    pass

    # ============================================================
    # ** HANDSHAKE **
    # ============================================================

    def send_handshake_request(self):
        """
        JOINER uses this to initiate the handshake AFTER discovering a host.
        """
        if self.role != "JOINER":
            return

        if not self.peer_addr:
            return

        fields = {"message_type": "HANDSHAKE_REQUEST"}
        ok, seq = self._send_reliable(fields)
        self._print_message(fields, seq)
    
    def _on_handshake_request(self, msg, addr):
        """
        Only HOST receives this.
        """
        if self.role != "HOST":
            return

        import random
        seed = random.randint(1, 999999)

        # HOST must also seed its RNG with the same seed
        from . import game_logic
        game_logic.set_seed(seed)

        self.peer_addr = addr
        fields = {
            "message_type": "HANDSHAKE_RESPONSE",
            "seed": seed
        }
        ok, seq = self._send_reliable(fields)
        self._print_message(fields, seq)
        
        # Transition to waiting for battle setup
        self.state = "WAITING_FOR_SETUP"
        print(f"\n[{self.local_name}]")
        print("message_type: HANDSHAKE_COMPLETE")
        print("role: HOST")
        print("state: WAITING_FOR_SETUP")
        print(f"seed: {seed}")

    def _on_handshake_response(self, msg):
        """
        JOINER receives this.
        """
        if self.role != "JOINER":
            return

        self._print_incoming_header()
        print("message_type: HANDSHAKE_RESPONSE")

        ok, missing = require_fields(msg, ["seed"])
        if not ok:
            print(f"[SM] Missing field in handshake response: {missing}")
            return

        seed = int(msg["seed"])
        # Seed BOTH RNG instances in game logic
        # We assume game_logic.py has a set_seed() function
        from . import game_logic
        game_logic.set_seed(seed)

        # Transition to waiting for setup exchange
        self.state = "WAITING_FOR_SETUP"
        print(f"\n[{self.local_name}]")
        print("message_type: HANDSHAKE_COMPLETE")
        print("role: JOINER")
        print("state: WAITING_FOR_SETUP")
        print(f"seed: {seed}")

    def _on_spectator_request(self, msg, addr):
        """
        Spectators just join and receive all battle/chat events.
        """
        # Record spectator address and optional name
        if addr not in self.spectators:
            self.spectators.append(addr)
        provided = msg.get("sender_name") or msg.get("trainer_name")
        if isinstance(provided, str) and provided:
            self.spectator_names[addr] = provided
        self.last_incoming_addr = addr
        self._print_incoming_header()
        print("message_type: SPECTATOR_JOINED")
        print(f"address: {addr}")

    # ============================================================
    # ** BATTLE SETUP **
    # ============================================================

    def send_battle_setup(self, pokemon: BattlePokemon, stat_boosts: Dict[str, int]):
        """
        Called by the UI or main.py after handshake.
        """
        self.local_pokemon = pokemon

        fields = {
            "message_type": "BATTLE_SETUP",
            "communication_mode": "P2P",
            "pokemon_name": pokemon.name,
            "pokemon": pokemon.to_json(),
            "stat_boosts": str(stat_boosts),
            "trainer_name": self.local_name,
        }
        ok, seq = self._send_reliable(fields)
        self._print_message(fields, seq)
        
        # Check if we can transition now (if we already received opponent's setup)
        if self.local_pokemon and self.remote_pokemon:
            self.state = "WAITING_FOR_MOVE"
            
            if self.role == "HOST":
                self.turn_owner = "LOCAL"
            else:
                self.turn_owner = "REMOTE"

    def _on_battle_setup(self, msg):
        # Cache remote trainer name if provided
        rn = msg.get("trainer_name")
        if rn and rn != self.local_name:
            self.remote_name = rn
        self._print_incoming_header()
        print("message_type: BATTLE_SETUP")

        ok, missing = require_fields(
            msg,
            ["pokemon", "pokemon_name"]
        )
        if not ok:
            print("[SM] Missing:", missing)
            return

        # Load remote Pokémon
        try:
            self.remote_pokemon = BattlePokemon.from_json(msg["pokemon"])
        except Exception as e:
            print(f"[SM] Error parsing remote pokemon: {e}")
            return

        print("[SM] Remote Pokémon:", self.remote_pokemon.name)

        # If both Pokémon are ready, begin battle
        if self.local_pokemon and self.remote_pokemon:
            print(f"\n[{self.local_name}]")
            print("message_type: BATTLE_START")
            print(f"local_pokemon: {self.local_pokemon.name}")
            print(f"remote_pokemon: {self.remote_pokemon.name}")
            self.state = "WAITING_FOR_MOVE"

            if self.role == "HOST":
                self.turn_owner = "LOCAL"
                print(f"\n[{self.local_name}]")
                print("message_type: TURN_ANNOUNCE")
                print("turn_owner: LOCAL")
            else:
                self.turn_owner = "REMOTE"
                print(f"\n[{self.local_name}]")
                print("message_type: TURN_ANNOUNCE")
                print("turn_owner: REMOTE")

    # ============================================================
    # ** TURN 1: ATTACK ANNOUNCE **
    # ============================================================

    def send_attack(self, move_name: str):
        """
        Called only when it is OUR turn.
        """
        if self.turn_owner != "LOCAL":
            return
        if self.state != "WAITING_FOR_MOVE":
            return

        # Save last announced move so attacker can use it later
        self.last_announced_move = move_name

        fields = {
            "message_type": "ATTACK_ANNOUNCE",
            "move_name": move_name
        }
        ok, seq = self._send_reliable(fields)
        self._print_message(fields, seq)

        self.state = "WAITING_FOR_DEFENSE"

    def _on_attack_announce(self, msg):
        self._print_incoming_header()
        print("message_type: ATTACK_ANNOUNCE")
        if "move_name" in msg:
            print(f"move_name: {msg['move_name']}")
        if "sequence_number" in msg:
            print(f"sequence_number: {msg['sequence_number']}")

        if self.turn_owner != "REMOTE":
            pass

        ok, missing = require_fields(msg, ["move_name"])
        if not ok:
            return

        self.remote_move = msg["move_name"]

        # Spectators should not participate (no defense announce, no state change)
        if self.role == "SPECTATOR":
            return

        # Immediately send defense announce and relay to spectators
        fields = {"message_type": "DEFENSE_ANNOUNCE"}
        ok, seq = self._send_reliable(fields)
        self._print_message(fields, seq)

        # Enter processing turn and trigger sending of calculation report on next tick
        self.state = "PROCESSING_TURN"

        # Try to send calculation immediately if possible (defender calculates now)
        # The tick() function also ensures the report is sent if not already.
        try:
            self.send_calculation_report(self.remote_move)
        except Exception as e:
            pass

    # ============================================================
    # ** TURN 2: DEFENSE ANNOUNCE **
    # ============================================================

    def _on_defense_announce(self, msg):
        self._print_incoming_header()
        print("message_type: DEFENSE_ANNOUNCE")
        if "sequence_number" in msg:
            print(f"sequence_number: {msg['sequence_number']}")

        if self.turn_owner != "LOCAL":
            pass

        # Spectators should not enter processing or calculate
        if self.role == "SPECTATOR":
            return

        self.state = "PROCESSING_TURN"

        # Attacker should now send their calculation report (use last_announced_move)
        if self.last_announced_move:
            try:
                self.send_calculation_report(self.last_announced_move)
            except Exception as e:
                pass

    # ============================================================
    # ** TURN 3: DAMAGE CALCULATION / REPORT **
    # ============================================================

    def send_calculation_report(self, move_name: str):
        """
        Called once both sides reached PROCESSING_TURN.

        This function determines whether the local peer is the attacker or the
        defender and calculates damage accordingly.
        """
        if not self.local_pokemon or not self.remote_pokemon:
            raise RuntimeError("Both local and remote Pokémon must be set before calculating damage")

        # Look up the move object from game_logic
        from . import game_logic
        move = game_logic.get_move(move_name)
        if not move:
            raise RuntimeError(f"Move '{move_name}' not found in move database")

        # Determine if local is the attacker or defender.
        # If turn_owner == "LOCAL" then the local peer issued the ATTACK_ANNOUNCE.
        local_is_attacker = (self.turn_owner == "LOCAL")

        if local_is_attacker:
            attacker = self.local_pokemon
            defender = self.remote_pokemon
            attacker_name = attacker.name
            # compute damage dealt to remote
            dmg = calculate_damage(attacker, defender, move)
            defender.hp -= dmg
            attacker_remaining = attacker.hp
            defender_remaining = max(defender.hp, 0)
        else:
            # local is defender — remote attacked
            attacker = self.remote_pokemon
            defender = self.local_pokemon
            attacker_name = attacker.name
            dmg = calculate_damage(attacker, defender, move)
            defender.hp -= dmg
            # note: attacker_remaining is remote's HP (unchanged by this local calculation)
            attacker_remaining = attacker.hp
            defender_remaining = max(defender.hp, 0)

        # Build status message with effectiveness wording
        effectiveness_msg = ""
        try:
            eff = game_logic.get_type_effectiveness(move.type, defender)
            if eff == 0:
                effectiveness_msg = " It had no effect."
            elif eff >= 1.5:
                effectiveness_msg = " It was super effective!"
            elif eff < 1.0:
                effectiveness_msg = " It was not very effective."
        except Exception:
            pass

        report = {
            "message_type": "CALCULATION_REPORT",
            "attacker": attacker_name,
            "move_used": move_name,
            "remaining_health": int(attacker_remaining),
            "damage_dealt": int(dmg),
            "defender_hp_remaining": int(defender_remaining),
            "status_message": f"{attacker_name} used {move_name}!{effectiveness_msg}",
        }

        self.local_calc_report = report

        # Send the report reliably
        ok, seq = self._send_reliable(report)
        
        # Print outgoing message in RFC format with sequence_number
        self._print_message(report, seq)

        # If this damage caused a faint locally, send GAME_OVER to peer
        if defender.hp <= 0:
            loser = defender.name
            winner = attacker.name
            fields = {
                "message_type": "GAME_OVER",
                "winner": winner,
                "loser": loser
            }
            ok, seq = self._send_reliable(fields)
            self._print_message(fields, seq)

    def _on_calculation_report(self, msg):
        self._print_incoming_header()
        print("message_type: CALCULATION_REPORT")
        if "attacker" in msg:
            print(f"attacker: {msg['attacker']}")
        if "move_used" in msg:
            print(f"move_used: {msg['move_used']}")
        if "damage_dealt" in msg:
            print(f"damage_dealt: {msg['damage_dealt']}")
        if "defender_hp_remaining" in msg:
            print(f"defender_hp_remaining: {msg['defender_hp_remaining']}")
        if "sequence_number" in msg:
            print(f"sequence_number: {msg['sequence_number']}")

        ok, missing = require_fields(
            msg, 
            ["attacker", "move_used", "damage_dealt", "defender_hp_remaining"]
        )
        if not ok:
            return

        self.last_calc_report_remote = msg

        # If we have not calculated our local version yet, wait.
        if self.local_calc_report is None:
            return

        # Compare for discrepancy
        local = self.local_calc_report
        remote = msg

        try:
            local_dmg = int(local["damage_dealt"])
            remote_dmg = int(remote["damage_dealt"])
            local_def_hp = int(local["defender_hp_remaining"])
            remote_def_hp = int(remote["defender_hp_remaining"])
        except Exception:
            return

        if (local_dmg == remote_dmg and local_def_hp == remote_def_hp):
            # Synchronized
            fields = {"message_type": "CALCULATION_CONFIRM"}
            ok, seq = self._send_reliable(fields)
            self._print_message(fields, seq)
        else:
            # Send our calculated values for resolution
            fields = {
                "message_type": "RESOLUTION_REQUEST",
                "attacker": local.get("attacker"),
                "move_used": local.get("move_used"),
                "damage_dealt": str(local.get("damage_dealt")),
                "defender_hp_remaining": str(local.get("defender_hp_remaining"))
            }
            ok, seq = self._send_reliable(fields)
            self._print_message(fields, seq)

    # ============================================================
    # ** TURN 4: CONFIRMATION / RESOLUTION **
    # ============================================================

    def _on_calculation_confirm(self, msg):
        self._print_incoming_header()
        print("message_type: CALCULATION_CONFIRM")
        if "sequence_number" in msg:
            print(f"sequence_number: {msg['sequence_number']}")

        # Turn ends — reverse turn ownership
        self.turn_owner = "REMOTE" if self.turn_owner == "LOCAL" else "LOCAL"

        self.local_calc_report = None
        self.last_calc_report_remote = None
        self.state = "WAITING_FOR_MOVE"

    def _on_resolution_request(self, msg):
        self._print_incoming_header()
        print("message_type: RESOLUTION_REQUEST")
        if "sequence_number" in msg:
            print(f"sequence_number: {msg['sequence_number']}")

        # Accept peer's corrected calculation
        try:
            dmg = int(msg["damage_dealt"])
            hp = int(msg["defender_hp_remaining"])
        except Exception:
            return

        # Update the defender's HP according to the resolution (msg describes defender_hp_remaining)
        # We must determine which Pokémon was defender in that report.
        defender_name = None
        if "attacker" in msg:
            attacker_name = msg["attacker"]
            # If attacker matches our remote, then defender is local; otherwise defender is remote
            if self.remote_pokemon and attacker_name == self.remote_pokemon.name:
                # remote attacked local
                if self.local_pokemon:
                    self.local_pokemon.hp = hp
                    defender_name = self.local_pokemon.name
            else:
                if self.remote_pokemon:
                    self.remote_pokemon.hp = hp
                    defender_name = self.remote_pokemon.name

        # Turn ends normally
        self.turn_owner = "REMOTE" if self.turn_owner == "LOCAL" else "LOCAL"
        self.state = "WAITING_FOR_MOVE"

        print(f"[SM] Resolution applied to {defender_name}, hp set to {hp}")

    # ============================================================
    # ** GAME OVER **
    # ============================================================

    def _on_game_over(self, msg):
        self._print_incoming_header()
        print("message_type: GAME_OVER")
        if "winner" in msg:
            print(f"winner: {msg['winner']}")
        if "loser" in msg:
            print(f"loser: {msg['loser']}")
        if "sequence_number" in msg:
            print(f"sequence_number: {msg['sequence_number']}")
        
        print("\nWinner:", msg.get("winner"))
        print("\nLoser:", msg.get("loser"))
        self.running = False

    # ============================================================
    # ** CHAT MESSAGES **
    # ============================================================

    def _on_chat(self, msg):
        # Update name caches from chat
        sender = msg.get("sender_name")
        if sender and sender != self.local_name:
            addr = self.last_incoming_addr
            if self.role == "HOST" and addr is not None and (self.peer_addr is None or addr != self.peer_addr):
                self.spectator_names[addr] = sender
            else:
                if not self.remote_name:
                    self.remote_name = sender
        self._print_incoming_header()
        print("message_type: CHAT_MESSAGE")
        if "sender_name" in msg:
            print(f"sender_name: {msg['sender_name']}")
        if "content_type" in msg:
            print(f"content_type: {msg['content_type']}")
        if msg.get("content_type") == "TEXT":
            if "message_text" in msg:
                print(f"message_text: {msg.get('message_text')}")
        elif msg.get("content_type") == "STICKER":
            sticker_data = msg.get("sticker_data")
            if sticker_data:
                filename = chat.save_sticker_to_file(sticker_data)
                print(f"sticker_data: [Base64 encoded, saved to {filename}]")
            else:
                print("sticker_data: [No data]")
        if "sequence_number" in msg:
            print(f"sequence_number: {msg['sequence_number']}")

    def send_chat_text(self, sender_name: str, text: str):
        """
        Send a TEXT chat message to the peer.

        Args:
            sender_name: Name of the sender
            text: Message text content
        """
        msg_dict = chat.make_text_message(sender_name, text)
        ok, seq = self.r.send_reliable(self.transport, msg_dict, self.peer_addr)
        
        # Print outgoing message in RFC format
        print(f"\n[{self.local_name}]")
        print("message_type: CHAT_MESSAGE")
        print(f"sender_name: {sender_name}")
        print("content_type: TEXT")
        print(f"message_text: {text}")
        if seq is not None:
            print(f"sequence_number: {seq}")

    def send_chat_sticker(self, sender_name: str, sticker_bytes: bytes):
        """
        Send a STICKER chat message to the peer.

        Args:
            sender_name: Name of the sender
            sticker_bytes: Raw binary sticker data (e.g., PNG file bytes)
        """
        msg_dict = chat.make_sticker_message(sender_name, sticker_bytes)
        ok, seq = self.r.send_reliable(self.transport, msg_dict, self.peer_addr)
        
        # Print outgoing message in RFC format
        print(f"\n[{self.local_name}]")
        print("message_type: CHAT_MESSAGE")
        print(f"sender_name: {sender_name}")
        print("content_type: STICKER")
        print(f"sticker_data: [Base64 encoded, {len(sticker_bytes)} bytes]")
        if seq is not None:
            print(f"sequence_number: {seq}")
