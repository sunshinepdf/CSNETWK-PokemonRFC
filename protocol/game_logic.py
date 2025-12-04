"""
protocol/game_logic.py

Implements all battle-related game logic:

- Pokémon stats (loaded from pokemon.csv via PokemonDatabase)
- Damage calculation (RFC Section 6)
- Type effectiveness (from official CSV data)
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
from typing import List, Dict, Any, Optional

from .pokemon_database import PokemonDatabase, PokemonStats

# ============================================================
# RNG SYNC UTILITIES
# ============================================================

_rng_seed = None

def set_seed(seed: int):
    global _rng_seed
    _rng_seed = seed
    random.seed(seed)


def rand() -> float:
    return random.random()


def rand_range(a: int, b: int) -> int:
    return random.randint(a, b)


def chance(p: float) -> bool:
    return rand() < p


# ============================================================
# MOVE STRUCTURE
# ============================================================

@dataclass
class Move:
    name: str
    power: float
    damage_category: str   # "physical", "special", "status"
    type: str


# ============================================================
# BATTLE POKEMON STRUCTURE
# Stores both battle stats AND full CSV defensive data.
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

    # Damage boost counters
    special_attack_uses: int = 0
    special_defense_uses: int = 0

    # Store full defensive chart from CSV
    effectiveness: Dict[str, float] = None

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
            "effectiveness": self.effectiveness,
        })

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
            effectiveness=d.get("effectiveness", {}),
        )

    def is_fainted(self) -> bool:
        return self.hp <= 0

    def apply_sp_atk_boost(self):
        if self.special_attack_uses > 0:
            self.special_attack_uses -= 1
            self.special_attack *= 1.3

    def apply_sp_def_boost(self):
        if self.special_defense_uses > 0:
            self.special_defense_uses -= 1
            self.special_defense *= 1.3


# ============================================================
# CSV-BASED TYPE EFFECTIVENESS
# ============================================================

# MOVE TYPE → against_* column map
# lowercase move types are used

def get_type_effectiveness(move_type: str, defender: BattlePokemon) -> float:
    key = f"against_{move_type.lower()}"
    return defender.effectiveness.get(key, 1.0)


# ============================================================
# DAMAGE FORMULA
# ============================================================

def calculate_damage(attacker: BattlePokemon, defender: BattlePokemon, move: Move) -> int:
    # Status move deals no damage
    if move.damage_category == "status":
        return 0

    # Apply boosts for special attacks/defenses
    if move.damage_category == "special":
        attacker.apply_sp_atk_boost()
        defender.apply_sp_def_boost()

    # Select stats
    if move.damage_category == "physical":
        atk_stat = attacker.attack
        def_stat = defender.defense
    else:
        atk_stat = attacker.special_attack
        def_stat = defender.special_defense

    def_stat = max(def_stat, 1)

    # Use CSV effectiveness values
    typem = get_type_effectiveness(move.type, defender)

    raw = (move.power * atk_stat * typem) / def_stat
    dmg = int(raw)

    return max(1, dmg)


# ============================================================
# DATABASES
# ============================================================

POKEMON_DB: Dict[str, Dict[str, Any]] = {}
MOVES_DB: Dict[str, Move] = {}
pokemon_database: Optional[PokemonDatabase] = None


def initialize_databases(pokemon_csv: str = "pokemon.csv", moves_csv: str = "moves.csv", verbose: bool = False):
    global pokemon_database, POKEMON_DB

    try:
        pokemon_database = PokemonDatabase(pokemon_csv, verbose=verbose)
        POKEMON_DB.clear()

        for name, stats in pokemon_database.data.items():
            # Collect all defensive effectiveness fields
            eff = {}
            for col, value in stats.__dict__.items():
                if col.startswith("against_"):
                    eff[col] = float(value)

            POKEMON_DB[stats.name] = {
                "name": stats.name,
                "hp": stats.hp,
                "max_hp": stats.hp,
                "attack": stats.attack,
                "special_attack": stats.sp_attack,
                "defense": stats.defense,
                "special_defense": stats.sp_defense,
                "types": stats.get_types_list(),
                "effectiveness": eff,
            }

        if verbose:
            print(f"[Game] Loaded {len(POKEMON_DB)} Pokémon from CSV with defensive charts")

    except Exception as e:
        print(f"[Error] Failed loading Pokémon CSV: {e}")
        POKEMON_DB.clear()

    load_moves_from_csv(moves_csv, verbose=verbose)


# ============================================================
# MOVES
# ============================================================

def load_moves_from_csv(filepath: str = "moves.csv", verbose: bool = False):
    global MOVES_DB

    try:
        import csv
        MOVES_DB.clear()
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                MOVES_DB[row['name'].strip()] = Move(
                    name=row['name'].strip(),
                    power=float(row['power']),
                    damage_category=row['damage_category'].strip(),
                    type=row['type'].strip(),
                )
        if verbose:
            print(f"[Game] Loaded {len(MOVES_DB)} moves from CSV")

    except Exception:
        create_default_moves()
        if verbose:
            print(f"[Game] Using default moves ({len(MOVES_DB)})")


def create_default_moves():
    global MOVES_DB
    MOVES_DB = {
        "Thunderbolt": Move("Thunderbolt", 90.0, "special", "electric"),
        "Flamethrower": Move("Flamethrower", 90.0, "special", "fire"),
        "Aqua Jet": Move("Aqua Jet", 40.0, "physical", "water"),
        "Grass Knot": Move("Grass Knot", 100.0, "special", "grass"),
        "Earthquake": Move("Earthquake", 100.0, "physical", "ground"),
        "Shadow Ball": Move("Shadow Ball", 80.0, "special", "ghost"),
        "Psychic": Move("Psychic", 90.0, "special", "psychic"),
        "Tackle": Move("Tackle", 40.0, "physical", "normal"),
        "Scratch": Move("Scratch", 40.0, "physical", "normal"),
    }


# ============================================================
# HELPERS
# ============================================================

def create_pokemon(name: str, sp_atk_uses: int = 5, sp_def_uses: int = 5) -> Optional[BattlePokemon]:
    if name not in POKEMON_DB:
        return None

    d = POKEMON_DB[name]
    return BattlePokemon(
        name=d['name'],
        max_hp=d['max_hp'],
        hp=d['max_hp'],
        attack=d['attack'],
        special_attack=d['special_attack'],
        defense=d['defense'],
        special_defense=d['special_defense'],
        types=d['types'],
        special_attack_uses=sp_atk_uses,
        special_defense_uses=sp_def_uses,
        effectiveness=d['effectiveness']
    )


def get_move(name: str) -> Optional[Move]:
    return MOVES_DB.get(name)
