"""
protocol/game_logic.py

Implements all battle-related game logic:

- Pokémon stats
- Damage calculation (RFC Section 6)
- Type effectiveness
- Special attack/defense boosts
- Fainting detection
- RNG seed sync

This module does NOT:
- handle networking
- handle reliability
- enforce protocol flow
- manage turn order

It is used by the state machine to compute damage,
synchronize battle state, and check victory conditions.
"""

import json
import random
from dataclasses import dataclass
from typing import List, Dict, Any


# ============================================================
# RNG SYNC (Handshake sets seed for both peers)
# ============================================================

def set_seed(seed: int):
    """Called by state_machine after receiving HANDSHAKE_RESPONSE."""
    random.seed(seed)


# ============================================================
# Type Effectiveness Table (minimal example)
# Expand with more types if needed
# ============================================================

TYPE_EFFECTIVENESS = {
    "Fire":     {"Fire": 0.5, "Water": 0.5, "Grass": 2.0, "Electric": 1.0},
    "Water":    {"Fire": 2.0, "Water": 0.5, "Grass": 0.5, "Electric": 1.0},
    "Grass":    {"Fire": 0.5, "Water": 2.0, "Grass": 0.5, "Electric": 1.0},
    "Electric": {"Fire": 1.0, "Water": 2.0, "Grass": 0.5, "Electric": 0.5},
}

def type_effect(move_type: str, defender_types: List[str]) -> float:
    """
    Returns the final type effectiveness multiplier based on the move's type.
    If a type is missing from the table, default to 1.0.
    """
    if move_type not in TYPE_EFFECTIVENESS:
        return 1.0

    mult = 1.0
    table = TYPE_EFFECTIVENESS[move_type]

    for t in defender_types:
        mult *= table.get(t, 1.0)

    return mult


# ============================================================
# Move Data (placeholder — customize as needed)
# ============================================================

@dataclass
class Move:
    name: str
    power: float
    damage_category: str   # "physical" or "special"
    type: str


# ============================================================
# Pokémon Data Structure
# ============================================================

@dataclass
class BattlePokemon:
    name: str
    max_hp: int
    hp: int
    attack: float
    special_attack: float
    defense: float
    special_defense: float
    types: List[str]

    special_attack_uses: int = 0
    special_defense_uses: int = 0

    # ----------------------------------
    # Convert to JSON for BATTLE_SETUP
    # ----------------------------------
    def to_json(self) -> str:
        return json.dumps({
            "name": self.name,
            "max_hp": self.max_hp,
            "hp": self.hp,
            "attack": self.attack,
            "special_attack": self.special_attack,
            "defense": self.defense,
            "special_defense": self.special_defense,
            "types": self.types,
            "special_attack_uses": self.special_attack_uses,
            "special_defense_uses": self.special_defense_uses,
        })

    # ----------------------------------
    # Load JSON from peer
    # ----------------------------------
    @staticmethod
    def from_json(s: str):
        d = json.loads(s)
        return BattlePokemon(
            name=d["name"],
            max_hp=d["max_hp"],
            hp=d["hp"],
            attack=d["attack"],
            special_attack=d["special_attack"],
            defense=d["defense"],
            special_defense=d["special_defense"],
            types=d["types"],
            special_attack_uses=d.get("special_attack_uses", 0),
            special_defense_uses=d.get("special_defense_uses", 0),
        )

    # ----------------------------------
    # Fainting
    # ----------------------------------
    def is_fainted(self) -> bool:
        return self.hp <= 0

    # ----------------------------------
    # Stat boost consumption
    # ----------------------------------
    def boost_sp_atk(self):
        if self.special_attack_uses > 0:
            self.special_attack_uses -= 1
            self.special_attack *= 1.3

    def boost_sp_def(self):
        if self.special_defense_uses > 0:
            self.special_defense_uses -= 1
            self.special_defense *= 1.3


# ============================================================
# DAMAGE CALCULATION (RFC Section 6)
# ============================================================

def calculate_damage(attacker: BattlePokemon, defender: BattlePokemon, move: Move) -> int:
    """
    RFC formula:

    Damage = (BasePower × AttackerStat × Type1Effect × Type2Effect) / DefenderStat

    - Uses attack or special attack based on damage_category.
    - Applies special_attack or special_defense boosts if available.
    """
    # Choose stats
    if move.damage_category == "physical":
        atk_stat = attacker.attack
        def_stat = defender.defense
    else:
        atk_stat = attacker.special_attack
        def_stat = defender.special_defense

    # Compute type multiplier
    t_mult = type_effect(move.type, defender.types)

    raw_damage = (move.power * atk_stat * t_mult) / max(def_stat, 1)

    # RFC does not specify rounding behavior — typical is floor()
    damage = max(1, int(raw_damage))

    return damage
