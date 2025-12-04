"""
protocol/state_machine.py

This is the central battle controller for PokeProtocol.
It implements:

- Handshake
- Battle setup
- Turn-based state transitions
- Attack/defense announce
- Calculation/reports
- Discrepancy resolution
- Game over detection
- Chat message integration
- Spectator support
- Integration with reliability layer

It does NOT:
- parse/encode messages   (messages.py)
- do socket I/O           (udp_transport.py)
- calculate damage        (game_logic.py)
"""

from typing import Dict, Tuple, Optional, Any
from .messages import (
    decode_message,
    encode_message,
    require_fields,
    parse_int_field,
)
from .reliability import ReliabilityLayer, ReliabilityError
from .game_logic import BattlePokemon, calculate_damage
from chat import ChatHandler


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

        # Storage for last received calculation report
        self.last_calc_report_remote: Optional[Dict[str, Any]] = None
        self.local_calc_report: Optional[Dict[str, Any]] = None

        # Game state flags
        self.state = "SETUP"
        self.running = True

    # ----------------------------
    # Helper for sending messages
    # ----------------------------
    def _send_reliable(self, fields: Dict[str, Any]):
        if not self.peer_addr:
            return False
        ok, _seq = self.r.send_reliable(self.transport, fields, self.peer_addr)
        return ok

    # ----------------------------
    # Tick called every loop
    # ----------------------------
    def tick(self):
        """
        Called from the main loop. Runs retransmission timers.
        """
        try:
            self.r.tick(self.transport)
        except ReliabilityError:
            print("[StateMachine] Peer unresponsive. Ending battle.")
            self.running = False

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

        print("[SM] Handshake complete.")

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
        self.remote_pokemon = BattlePokemon.from_json(msg["pokemon"])
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
            return
        if self.state != "WAITING_FOR_MOVE":
            return

        print("[SM] Sending ATTACK_ANNOUNCE:", move_name)

        self._send_reliable({
            "message_type": "ATTACK_ANNOUNCE",
            "move_name": move_name
        })

        self.state = "WAITING_FOR_DEFENSE"

    def _on_attack_announce(self, msg):
        print("[SM] Received ATTACK_ANNOUNCE")

        if self.turn_owner != "REMOTE":
            return

        ok, missing = require_fields(msg, ["move_name"])
        if not ok:
            print("[SM] Missing:", missing)
            return

        self.remote_move = msg["move_name"]

        # Immediately send defense announce
        self._send_reliable({
            "message_type": "DEFENSE_ANNOUNCE"
        })

        self.state = "PROCESSING_TURN"

    # ============================================================
    # ** TURN 2: DEFENSE ANNOUNCE **
    # ============================================================

    def _on_defense_announce(self, msg):
        print("[SM] Received DEFENSE_ANNOUNCE")

        if self.turn_owner != "LOCAL":
            return

        self.state = "PROCESSING_TURN"

    # ============================================================
    # ** TURN 3: DAMAGE CALCULATION / REPORT **
    # ============================================================

    def send_calculation_report(self, move_name: str):
        """
        Called once both sides reached PROCESSING_TURN.
        """
        dmg = calculate_damage(self.local_pokemon, self.remote_pokemon, move_name)
        self.remote_pokemon.hp -= dmg

        report = {
            "message_type": "CALCULATION_REPORT",
            "attacker": self.local_pokemon.name,
            "move_used": move_name,
            "remaining_health": self.local_pokemon.hp,
            "damage_dealt": dmg,
            "defender_hp_remaining": max(self.remote_pokemon.hp, 0),
            "status_message": f"{self.local_pokemon.name} used {move_name}!",
        }

        self.local_calc_report = report
        self._send_reliable(report)

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

        if (int(local["damage_dealt"]) == int(remote["damage_dealt"]) and
            int(local["defender_hp_remaining"]) == int(remote["defender_hp_remaining"])):
            # Synchronized
            self._send_reliable({"message_type": "CALCULATION_CONFIRM"})
        else:
            print("[SM] Mismatch detected, requesting resolution")
            self._send_reliable({
                "message_type": "RESOLUTION_REQUEST",
                "attacker": local["attacker"],
                "move_used": local["move_used"],
                "damage_dealt": local["damage_dealt"],
                "defender_hp_remaining": local["defender_hp_remaining"]
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
        dmg = int(msg["damage_dealt"])
        hp = int(msg["defender_hp_remaining"])
        self.remote_pokemon.hp = hp

        # Send ACK already done by reliability layer
        # Turn ends normally
        self.turn_owner = "REMOTE" if self.turn_owner == "LOCAL" else "LOCAL"
        self.state = "WAITING_FOR_MOVE"

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
            print("[Sticker Received] (Base64 omitted)")
