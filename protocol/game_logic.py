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
    # Dynamic scaling flags (for abilities treated as moves)
    scale_with_hp: bool = False
    hp_ratio: float = 0.5
    power_min: float = 35.0
    power_max: float = 110.0


# ============================================================
# MOVE POWER HELPERS
# ============================================================

def get_effective_move_power(move: Move, attacker: "BattlePokemon") -> float:
        """Return effective power for a move, applying any dynamic scaling.

        - If move.scale_with_hp is True, scales using attacker's max_hp * hp_ratio,
            clamped to [power_min, power_max].
        - Otherwise returns move.power as-is.
        """
        if getattr(move, 'scale_with_hp', False):
                scaled = attacker.max_hp * getattr(move, 'hp_ratio', 0.5)
                pmin = getattr(move, 'power_min', 35.0)
                pmax = getattr(move, 'power_max', 110.0)
                return max(pmin, min(pmax, scaled))
        return move.power


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

    abilities: List[str] = None
    special_attack_uses: int = 0
    special_defense_uses: int = 0
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
    # defender.effectiveness uses plain type keys from CSV (e.g., 'fire', 'water')
    return defender.effectiveness.get(move_type.lower(), 1.0)


# ============================================================
# DAMAGE FORMULA
# ============================================================

def calculate_damage(attacker: BattlePokemon, defender: BattlePokemon, move: Move) -> int:
    """
    RFC Section 6 Damage Calculation Formula:
    Damage = (BasePower * AttackerStat * TypeEffectiveness) / DefenderStat
    
    Where:
    - BasePower: move.power
    - AttackerStat: Attack (physical) or SpA (special)
    - TypeEffectiveness: from CSV against_* columns (already combines Type1 and Type2)
    - DefenderStat: Defense (physical) or SpD (special)
    
    Stat boosts (consumable resources):
    - If this is a SPECIAL move and attacker has special_attack_uses remaining:
      Consume 1 use and multiply AttackerStat by 1.3 (increases damage by 1.3x)
    - If this is a SPECIAL move and defender has special_defense_uses remaining:
      Consume 1 use and multiply DefenderStat by 1.3 (decreases damage by 1.3x)
    
    Each player has limited uses (typically 5) that are consumed throughout the battle.
    """
    # Status move deals no damage
    if move.damage_category == "status":
        return 0

    # Select stats based on move category
    if move.damage_category == "physical":
        atk_stat = attacker.attack
        def_stat = defender.defense
        # Ability: Huge Power (physical only)
        if attacker.abilities and any(a.lower() == 'huge power' for a in attacker.abilities):
            atk_stat *= 2
    else:
        atk_stat = attacker.special_attack
        def_stat = defender.special_defense

    # Apply boosts for special moves only (consume uses and apply multiplier)
    if move.damage_category == "special":
        if attacker.special_attack_uses > 0:
            attacker.special_attack_uses -= 1
            atk_stat *= 1.3
        if defender.special_defense_uses > 0:
            defender.special_defense_uses -= 1
            def_stat *= 1.3

    def_stat = max(def_stat, 1)

    # Get type effectiveness from CSV (combines both types automatically)
    type_effectiveness = get_type_effectiveness(move.type, defender)

    # Ability: Thick Fat (fire/ice)
    if defender.abilities and any(a.lower() == 'thick fat' for a in defender.abilities):
        if move.type.lower() in {'fire', 'ice'}:
            type_effectiveness *= 0.5

    # Compute effective power via shared helper
    effective_power = get_effective_move_power(move, attacker)

    raw = (effective_power * atk_stat * type_effectiveness) / def_stat
    dmg = int(raw)
    return max(1, dmg)


# ============================================================
# DATABASES
# ============================================================

POKEMON_DB: Dict[str, Dict[str, Any]] = {}
MOVES_DB: Dict[str, Move] = {}
pokemon_database: Optional[PokemonDatabase] = None


def initialize_databases(pokemon_csv: str = "pokemon.csv", verbose: bool = False):
    global pokemon_database, POKEMON_DB

    try:
        pokemon_database = PokemonDatabase(pokemon_csv, verbose=verbose)
        POKEMON_DB.clear()

        for name, stats in pokemon_database.data.items():
            # Use effectiveness map directly from CSV parser (plain type keys)
            eff = dict(stats.effectiveness)

            POKEMON_DB[stats.name] = {
                "name": stats.name,
                "hp": stats.hp,
                "max_hp": stats.hp,
                "attack": stats.attack,
                "special_attack": stats.sp_attack,
                "defense": stats.defense,
                "special_defense": stats.sp_defense,
                "types": stats.get_types_list(),
                "abilities": stats.abilities,
                "effectiveness": eff,
            }

        if verbose:
            print(f"[Game] Loaded {len(POKEMON_DB)} Pokémon from CSV with defensive charts")

    except Exception as e:
        print(f"[Error] Failed loading Pokémon CSV: {e}")
        POKEMON_DB.clear()

    load_moves_from_pokemon_csv(pokemon_csv, verbose=verbose)


# ============================================================
# MOVES
# ============================================================

# Move base power library - used to look up move power values
MOVE_POWER_DB = {
    "Tackle": 40.0,
    "Scratch": 40.0,
    "Flamethrower": 90.0,
    "Fire Punch": 75.0,
    "Aqua Jet": 40.0,
    "Hydro Pump": 110.0,
    "Grass Knot": 100.0,
    "Solar Beam": 120.0,
    "Thunderbolt": 90.0,
    "Thunder Punch": 75.0,
    "Ice Beam": 90.0,
    "Ice Punch": 75.0,
    "Close Combat": 120.0,
    "Karate Chop": 50.0,
    "Poison Powder": 75.0,
    "Cross Poison": 70.0,
    "Earthquake": 100.0,
    "Dig": 80.0,
    "Aerial Ace": 60.0,
    "Sky Attack": 140.0,
    "Psychic": 90.0,
    "Psybeam": 65.0,
    "X-Scissor": 81.0,
    "Megahorn": 120.0,
    "Rock Slide": 75.0,
    "Stone Edge": 100.0,
    "Shadow Ball": 80.0,
    "Shadow Claw": 70.0,
    "Dragon Claw": 80.0,
    "Dragon Pulse": 85.0,
    "Dark Pulse": 80.0,
    "Crunch": 80.0,
    "Iron Head": 80.0,
    "Flash Cannon": 80.0,
    "Dazzling Gleam": 80.0,
    "Play Rough": 90.0,
}

# Move type and category library
MOVE_TYPE_DB = {
    "Tackle": ("normal", "physical"),
    "Scratch": ("normal", "physical"),
    "Flamethrower": ("fire", "special"),
    "Fire Punch": ("fire", "physical"),
    "Aqua Jet": ("water", "physical"),
    "Hydro Pump": ("water", "special"),
    "Grass Knot": ("grass", "special"),
    "Solar Beam": ("grass", "special"),
    "Thunderbolt": ("electric", "special"),
    "Thunder Punch": ("electric", "physical"),
    "Ice Beam": ("ice", "special"),
    "Ice Punch": ("ice", "physical"),
    "Close Combat": ("fighting", "physical"),
    "Karate Chop": ("fighting", "physical"),
    "Poison Powder": ("poison", "special"),
    "Cross Poison": ("poison", "physical"),
    "Earthquake": ("ground", "physical"),
    "Dig": ("ground", "physical"),
    "Aerial Ace": ("flying", "physical"),
    "Sky Attack": ("flying", "physical"),
    "Psychic": ("psychic", "special"),
    "Psybeam": ("psychic", "special"),
    "X-Scissor": ("bug", "physical"),
    "Megahorn": ("bug", "physical"),
    "Rock Slide": ("rock", "physical"),
    "Stone Edge": ("rock", "physical"),
    "Shadow Ball": ("ghost", "special"),
    "Shadow Claw": ("ghost", "physical"),
    "Dragon Claw": ("dragon", "physical"),
    "Dragon Pulse": ("dragon", "special"),
    "Dark Pulse": ("dark", "special"),
    "Crunch": ("dark", "physical"),
    "Iron Head": ("steel", "physical"),
    "Flash Cannon": ("steel", "special"),
    "Dazzling Gleam": ("fairy", "special"),
    "Play Rough": ("fairy", "physical"),
}


def load_moves_from_pokemon_csv(pokemon_csv: str = "pokemon.csv", verbose: bool = False):
    """
    Load moves from each Pokemon's abilities column in the CSV.
    Each ability string is parsed (JSON list format) to extract move names.
    Move power and type info are looked up from MOVE_POWER_DB and MOVE_TYPE_DB.
    """
    global MOVES_DB
    MOVES_DB.clear()
    
    import csv
    import json
    
    try:
        with open(pokemon_csv, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                abilities_raw = row.get('abilities', '').strip()
                if not abilities_raw:
                    continue

                # Robust parse for abilities list represented like "['Overgrow', 'Chlorophyll']" or [ 'Shed Skin' ]
                ability_list: List[str] = []
                parsed = False
                if abilities_raw.startswith('[') and abilities_raw.endswith(']'):
                    try:
                        tmp = abilities_raw.replace("'", '"')
                        json_list = json.loads(tmp)
                        if isinstance(json_list, list):
                            ability_list = [str(x).strip() for x in json_list]
                            parsed = True
                    except Exception:
                        parsed = False
                if not parsed:
                    # Fallback: strip brackets and split by commas
                    s = abilities_raw.strip().strip('[]')
                    ability_list = [seg.strip().strip("'").strip('"') for seg in s.split(',') if seg.strip()]

                type1 = (row.get('type1') or 'normal').strip().lower()
                try:
                    hp_val = int(float(row.get('hp', '60')))
                except Exception:
                    hp_val = 60

                special_types = {'fire','water','grass','electric','psychic','ice','dragon','dark','fairy'}

                for move_name in ability_list:
                    if not move_name:
                        continue
                    # If this ability name maps to a known move, register it using static data
                    if move_name in MOVE_POWER_DB and move_name in MOVE_TYPE_DB:
                        move_type, move_category = MOVE_TYPE_DB[move_name]
                        move_power = MOVE_POWER_DB[move_name]
                        if move_name not in MOVES_DB:
                            MOVES_DB[move_name] = Move(
                                name=move_name,
                                power=move_power,
                                damage_category=move_category,
                                type=move_type
                            )
                        continue

                    # Otherwise, define a dynamic HP-scaled move for this ability
                    move_type = type1 or 'normal'
                    move_category = 'special' if move_type in special_types else 'physical'

                    # Determine ratio bounds based on HP to keep within typical move ranges
                    ratio = 0.5  # 50% of max HP
                    pmin = 35.0
                    pmax = 110.0
                    if move_name not in MOVES_DB:
                        MOVES_DB[move_name] = Move(
                            name=move_name,
                            power=0.0,
                            damage_category=move_category,
                            type=move_type,
                            scale_with_hp=True,
                            hp_ratio=ratio,
                            power_min=pmin,
                            power_max=pmax
                        )
        
        if verbose:
            print(f"[Game] Loaded {len(MOVES_DB)} moves from Pokemon CSV abilities")
    
    except Exception as e:
        print(f"[Error] Failed loading moves from CSV: {e}")
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
        abilities=d.get('abilities', []),
        special_attack_uses=sp_atk_uses,
        special_defense_uses=sp_def_uses,
        effectiveness=d['effectiveness']
    )


def get_move(name: str) -> Optional[Move]:
    return MOVES_DB.get(name)
