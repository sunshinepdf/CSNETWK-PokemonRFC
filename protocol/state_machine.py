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
    def __init__(self, transport, reliability: ReliabilityLayer, role: str):
        """
        role: "HOST", "JOINER", or "SPECTATOR"
        """
        self.transport = transport
        self.r = reliability
        self.role = role

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
            print("[SM] No peer address set — cannot send")
            return False
        ok, _seq = self.r.send_reliable(self.transport, fields, self.peer_addr)
        return ok

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
                    print(f"[SM] Error sending calculation report: {e}")

    # ----------------------------
    # Incoming dispatcher
    # ----------------------------
    def handle_incoming(self, incoming: Tuple[bytes, Tuple[str, int]]):
        data, addr = incoming
        msg = decode_message(data)
        message_type = msg.get("message_type")

        # Always send ACK if needed
        self.r.incoming_message(msg, addr, self.transport)

        # Save peer address if not set yet
        if self.peer_addr is None and self.role != "SPECTATOR":
            self.peer_addr = addr

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

    def _on_handshake_request(self, msg, addr):
        """
        Only HOST receives this.
        """
        if self.role != "HOST":
            return
        print("[SM] Received HANDSHAKE_REQUEST")

        import random
        seed = random.randint(1, 999999)

        self.peer_addr = addr
        self._send_reliable({
            "message_type": "HANDSHAKE_RESPONSE",
            "seed": seed
        })
        
        # Transition to waiting for battle setup
        self.state = "WAITING_FOR_SETUP"
        print("[SM] Handshake complete (HOST), waiting for battle setup...")

    def _on_handshake_response(self, msg):
        """
        JOINER receives this.
        """
        if self.role != "JOINER":
            return

        print("[SM] Received HANDSHAKE_RESPONSE")

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
        print("[SM] Handshake complete (JOINER), ready to exchange battle setup...")

    def _on_spectator_request(self, msg, addr):
        """
        Spectators just join and receive all battle/chat events.
        """
        print("[SM] Spectator joined:", addr)

    # ============================================================
    # ** BATTLE SETUP **
    # ============================================================

    def send_battle_setup(self, pokemon: BattlePokemon, stat_boosts: Dict[str, int]):
        """
        Called by the UI or main.py after handshake.
        """
        self.local_pokemon = pokemon

        self._send_reliable({
            "message_type": "BATTLE_SETUP",
            "communication_mode": "P2P",
            "pokemon_name": pokemon.name,
            "pokemon": pokemon.to_json(),
            "stat_boosts": str(stat_boosts),
        })

    def _on_battle_setup(self, msg):
        print("[SM] Received BATTLE_SETUP")

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
            print("[SM] Both battle setups done.")
            self.state = "WAITING_FOR_MOVE"

            if self.role == "HOST":
                self.turn_owner = "LOCAL"
            else:
                self.turn_owner = "REMOTE"

    # ============================================================
    # ** TURN 1: ATTACK ANNOUNCE **
    # ============================================================

    def send_attack(self, move_name: str):
        """
        Called only when it is OUR turn.
        """
        if self.turn_owner != "LOCAL":
            print("[SM] Not our turn to attack")
            return
        if self.state != "WAITING_FOR_MOVE":
            print("[SM] Cannot attack in current state:", self.state)
            return

        # Save last announced move so attacker can use it later
        self.last_announced_move = move_name

        print("[SM] Sending ATTACK_ANNOUNCE:", move_name)

        self._send_reliable({
            "message_type": "ATTACK_ANNOUNCE",
            "move_name": move_name
        })

        self.state = "WAITING_FOR_DEFENSE"

    def _on_attack_announce(self, msg):
        print("[SM] Received ATTACK_ANNOUNCE")

        if self.turn_owner != "REMOTE":
            # If turn_owner unknown or different, still accept but log
            print("[SM] Warning: received ATTACK_ANNOUNCE but turn_owner is", self.turn_owner)
            # continue — allow defender to proceed

        ok, missing = require_fields(msg, ["move_name"])
        if not ok:
            print("[SM] Missing:", missing)
            return

        self.remote_move = msg["move_name"]

        # Immediately send defense announce
        self._send_reliable({
            "message_type": "DEFENSE_ANNOUNCE"
        })

        # Enter processing turn and trigger sending of calculation report on next tick
        self.state = "PROCESSING_TURN"

        # Try to send calculation immediately if possible (defender calculates now)
        # The tick() function also ensures the report is sent if not already.
        try:
            self.send_calculation_report(self.remote_move)
        except Exception as e:
            print(f"[SM] Error during defender calculation: {e}")

    # ============================================================
    # ** TURN 2: DEFENSE ANNOUNCE **
    # ============================================================

    def _on_defense_announce(self, msg):
        print("[SM] Received DEFENSE_ANNOUNCE")

        if self.turn_owner != "LOCAL":
            # Attackers expect DEFENSE_ANNOUNCE when they had the turn; warn if not
            print("[SM] Warning: DEFENSE_ANNOUNCE received but turn_owner is", self.turn_owner)

        self.state = "PROCESSING_TURN"

        # Attacker should now send their calculation report (use last_announced_move)
        if self.last_announced_move:
            try:
                self.send_calculation_report(self.last_announced_move)
            except Exception as e:
                print(f"[SM] Error sending attacker calculation: {e}")

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

        # Determine if local is the attacker or defender.
        # If turn_owner == "LOCAL" then the local peer issued the ATTACK_ANNOUNCE.
        local_is_attacker = (self.turn_owner == "LOCAL")

        if local_is_attacker:
            attacker = self.local_pokemon
            defender = self.remote_pokemon
            attacker_name = attacker.name
            # compute damage dealt to remote
            dmg = calculate_damage(attacker, defender, move_name)
            defender.hp -= dmg
            attacker_remaining = attacker.hp
            defender_remaining = max(defender.hp, 0)
        else:
            # local is defender — remote attacked
            attacker = self.remote_pokemon
            defender = self.local_pokemon
            attacker_name = attacker.name
            dmg = calculate_damage(attacker, defender, move_name)
            defender.hp -= dmg
            # note: attacker_remaining is remote's HP (unchanged by this local calculation)
            attacker_remaining = attacker.hp
            defender_remaining = max(defender.hp, 0)

        report = {
            "message_type": "CALCULATION_REPORT",
            "attacker": attacker_name,
            "move_used": move_name,
            "remaining_health": int(attacker_remaining),
            "damage_dealt": int(dmg),
            "defender_hp_remaining": int(defender_remaining),
            "status_message": f"{attacker_name} used {move_name}!",
        }

        self.local_calc_report = report

        # Send the report reliably
        self._send_reliable(report)

        # If this damage caused a faint locally, send GAME_OVER to peer
        if defender.hp <= 0:
            loser = defender.name
            winner = attacker.name
            print(f"[SM] Detected faint: {loser} — sending GAME_OVER")
            self._send_reliable({
                "message_type": "GAME_OVER",
                "winner": winner,
                "loser": loser
            })

    def _on_calculation_report(self, msg):
        print("[SM] Received CALCULATION_REPORT")

        ok, missing = require_fields(
            msg, 
            ["attacker", "move_used", "damage_dealt", "defender_hp_remaining"]
        )
        if not ok:
            print("[SM] Missing:", missing)
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
            print("[SM] Invalid numbers in calculation reports")
            return

        if (local_dmg == remote_dmg and local_def_hp == remote_def_hp):
            # Synchronized
            self._send_reliable({"message_type": "CALCULATION_CONFIRM"})
        else:
            print("[SM] Mismatch detected, requesting resolution")
            # Send our calculated values for resolution
            self._send_reliable({
                "message_type": "RESOLUTION_REQUEST",
                "attacker": local.get("attacker"),
                "move_used": local.get("move_used"),
                "damage_dealt": str(local.get("damage_dealt")),
                "defender_hp_remaining": str(local.get("defender_hp_remaining"))
            })

    # ============================================================
    # ** TURN 4: CONFIRMATION / RESOLUTION **
    # ============================================================

    def _on_calculation_confirm(self, msg):
        print("[SM] Received CALCULATION_CONFIRM")

        # Turn ends — reverse turn ownership
        self.turn_owner = "REMOTE" if self.turn_owner == "LOCAL" else "LOCAL"

        self.local_calc_report = None
        self.last_calc_report_remote = None
        self.state = "WAITING_FOR_MOVE"

    def _on_resolution_request(self, msg):
        print("[SM] Received RESOLUTION_REQUEST")

        # Accept peer’s corrected calculation
        try:
            dmg = int(msg["damage_dealt"])
            hp = int(msg["defender_hp_remaining"])
        except Exception as e:
            print(f"[SM] Invalid resolution request values: {e}")
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
        print("[SM] Received GAME_OVER")
        print("Winner:", msg.get("winner"))
        print("Loser:", msg.get("loser"))
        self.running = False

    # ============================================================
    # ** CHAT MESSAGES **
    # ============================================================

    def _on_chat(self, msg):
        print(f"[CHAT] {msg.get('sender_name')}: ", end="")
        if msg.get("content_type") == "TEXT":
            print(msg.get("message_text"))
        elif msg.get("content_type") == "STICKER":
            sticker_data = msg.get("sticker_data")
            if sticker_data:
                filename = chat.save_sticker_to_file(sticker_data)
                print(f"[Sticker Received and saved to {filename}]")
            else:
                print("[Sticker Received] (No data)")

    def send_chat_text(self, sender_name: str, text: str):
        """
        Send a TEXT chat message to the peer.

        Args:
            sender_name: Name of the sender
            text: Message text content
        """
        msg_dict = chat.make_text_message(sender_name, text)
        self._send_reliable(msg_dict)
        print(f"[CHAT] Sent: {text}")

    def send_chat_sticker(self, sender_name: str, sticker_bytes: bytes):
        """
        Send a STICKER chat message to the peer.

        Args:
            sender_name: Name of the sender
            sticker_bytes: Raw binary sticker data (e.g., PNG file bytes)
        """
        msg_dict = chat.make_sticker_message(sender_name, sticker_bytes)
        self._send_reliable(msg_dict)
        print(f"[CHAT] Sent sticker ({len(sticker_bytes)} bytes)")
