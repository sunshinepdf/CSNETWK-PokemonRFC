"""
protocol/pokemon_database.py

Loads and provides access to Pokémon statistics and type effectiveness data from pokemon.csv.
"""

import csv
import os
from typing import Dict, Optional, List
from dataclasses import dataclass


@dataclass
class PokemonStats:
    """Represents a Pokémon's battle statistics and type effectiveness."""
    name: str
    hp: int
    attack: float
    defense: float
    sp_attack: float
    sp_defense: float
    speed: float
    type1: str
    type2: Optional[str]
    effectiveness: Dict[str, float]
    pokedex_number: int
    generation: int
    is_legendary: bool
    
    def get_types_list(self) -> List[str]:
        """Return list of types, filtering out None values."""
        types = [self.type1]
        if self.type2 and self.type2.lower() not in ['', 'none', 'null']:
            types.append(self.type2)
        return types


class PokemonDatabase:
    """Loads pokemon.csv and provides stats/effectiveness data."""
    
    def __init__(self, csv_path: str = "pokemon.csv", verbose: bool = False):
        """
        Initialize the Pokémon database.
        
        Args:
            csv_path: Path to the pokemon.csv file
            verbose: Enable verbose logging
        """
        self.data: Dict[str, PokemonStats] = {}
        self.verbose = verbose
        self._load_csv(csv_path)
    
    def _load_csv(self, path: str) -> None:
        """
        Load Pokémon data from CSV file.
        
        Args:
            path: Path to CSV file
        """
        if not os.path.exists(path):
            raise FileNotFoundError(f"Pokémon CSV file not found: {path}")
        
        with open(path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            loaded_count = 0
            for row_num, row in enumerate(reader, start=1):
                try:
                    # Extract type effectiveness data from against_* columns
                    effectiveness = {}
                    for key, value in row.items():
                        if key.startswith('against_'):
                            type_name = key.replace('against_', '')
                            try:
                                effectiveness[type_name] = float(value)
                            except (ValueError, TypeError):
                                effectiveness[type_name] = 1.0
                    
                    # Parse stats
                    hp_value = int(float(row['hp']))
                    
                    # Parse type2 (optional)
                    type2 = row.get('type2', '').strip()
                    if type2.lower() in ['', 'nan', 'none', 'null']:
                        type2 = None
                    
                    # Parse generation
                    try:
                        generation = int(row.get('generation', 1))
                    except (ValueError, TypeError):
                        generation = 1
                    
                    # Parse legendary status
                    is_legendary = False
                    legendary_val = row.get('is_legendary', '0')
                    if legendary_val:
                        try:
                            is_legendary = bool(int(float(legendary_val)))
                        except (ValueError, TypeError):
                            is_legendary = False
                    
                    pokemon = PokemonStats(
                        name=row['name'].strip(),
                        hp=hp_value,
                        attack=float(row['attack']),
                        defense=float(row['defense']),
                        sp_attack=float(row['sp_attack']),
                        sp_defense=float(row['sp_defense']),
                        speed=float(row['speed']),
                        type1=row['type1'].strip().lower(),
                        type2=type2.lower() if type2 else None,
                        effectiveness=effectiveness,
                        pokedex_number=int(row['pokedex_number']),
                        generation=generation,
                        is_legendary=is_legendary
                    )
                    
                    # Store with lowercase name for case-insensitive lookup
                    self.data[pokemon.name.lower()] = pokemon
                    loaded_count += 1
                    
                except (ValueError, KeyError) as e:
                    if self.verbose:
                        print(f"[PokemonDatabase] Skipping row {row_num}: {e}")
                    continue
            
            if self.verbose:
                print(f"[PokemonDatabase] Loaded {loaded_count} Pokémon from {path}")
    
    def get_pokemon(self, name: str) -> Optional[PokemonStats]:
        """
        Get Pokémon statistics by name (case-insensitive).
        
        Args:
            name: Pokémon name
            
        Returns:
            PokemonStats object or None if not found
        """
        return self.data.get(name.lower())
    
    def get_all_pokemon_names(self) -> List[str]:
        """
        Get list of all Pokémon names in the database.
        
        Returns:
            List of Pokémon names
        """
        return [pokemon.name for pokemon in self.data.values()]
    
    def get_pokemon_by_type(self, type_name: str) -> List[PokemonStats]:
        """
        Get all Pokémon of a specific type.
        
        Args:
            type_name: Type to filter by
            
        Returns:
            List of PokemonStats objects
        """
        type_name = type_name.lower()
        result = []
        
        for pokemon in self.data.values():
            if (pokemon.type1 == type_name or 
                (pokemon.type2 and pokemon.type2 == type_name)):
                result.append(pokemon)
        
        return result
