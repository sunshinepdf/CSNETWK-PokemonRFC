#!/usr/bin/env python3
"""
main.py

Complete PokeProtocol Battle Application

Features:
- Host or Join battles
- Broadcast mode for peer discovery
- Spectator mode
- Chat with text and stickers
- Full turn-based battle system
- Automatic game over detection
"""

import sys
import time
import threading
from typing import Optional

# Import protocol modules
from protocol.udp_transport import UDPTransport
from protocol.reliability import ReliabilityLayer
from protocol.state_machine import ProtocolStateMachine
from protocol.broadcast import BroadcastDiscovery
from protocol import game_logic

BROADCAST_PORT = 5556

class BattleApplication:
    """Main application controller."""

    def __init__(self):
        self.transport: Optional[UDPTransport] = None
        self.reliability: Optional[ReliabilityLayer] = None
        self.state_machine: Optional[ProtocolStateMachine] = None
        self.broadcast: Optional[BroadcastDiscovery] = None
        self.running = False
        self.player_name = "Player"

        # Threads
        self._network_thread: Optional[threading.Thread] = None
        self._broadcast_thread: Optional[threading.Thread] = None

    def setup(self, role: str, port: int, host_ip: Optional[str] = None, host_port: Optional[int] = None):
        """
        Initialize the application.

        Args:
            role: "HOST", "JOINER", or "SPECTATOR"
            port: Local port to bind
            host_ip: Remote host IP (for JOINER/SPECTATOR)
            host_port: Remote host port (for JOINER/SPECTATOR)
        """
        # Load game data
        print("[App] Loading Pokémon and moves from CSV...")
        game_logic.initialize_databases(pokemon_csv="pokemon.csv", moves_csv="moves.csv", verbose=True)

        # Initialize transport
        self.transport = UDPTransport(port, "0.0.0.0")
        self.transport.open()

        # Initialize reliability layer
        self.reliability = ReliabilityLayer(timeout=0.5, max_retries=3)

        # Initialize state machine
        self.state_machine = ProtocolStateMachine(
            self.transport,
            self.reliability,
            role
        )

        # Initialize broadcast for discovery (use separate port to avoid collisions)
        try:
            self.broadcast = BroadcastDiscovery(port=BROADCAST_PORT)
            self.broadcast.open()
        except Exception as e:
            print(f"[APP] Warning: broadcast discovery failed to open: {e}")
            self.broadcast = None

        # If joining, send handshake request
        if role == "JOINER" and host_ip and host_port:
            print(f"[APP] Sending HANDSHAKE_REQUEST to {host_ip}:{host_port}")
            self.state_machine.peer_addr = (host_ip, host_port)
            self.state_machine.send_handshake_request()
            if not ok:
                print("[App] Warning: initial HANDSHAKE_REQUEST may not have been sent")

        # If spectating, send spectator request
        elif role == "SPECTATOR" and host_ip and host_port:
            print(f"[App] Sending SPECTATOR_REQUEST to {host_ip}:{host_port}")
            self.state_machine.peer_addr = (host_ip, host_port)
            ok, _ = self.reliability.send_reliable(
                self.transport,
                {"message_type": "SPECTATOR_REQUEST"},
                (host_ip, host_port)
            )
            if not ok:
                print("[App] Warning: initial SPECTATOR_REQUEST may not have been sent")

        self.running = True

    def announce_game_loop(self):
        """Periodically announce game availability (for HOST)."""
        # Defensive: ensure broadcast and transport exist
        while self.running and self.state_machine and self.state_machine.role == "HOST":
            try:
                if self.broadcast and self.transport:
                    self.broadcast.announce_game(self.player_name, self.transport.port)
            except Exception as e:
                print(f"[App] Error while announcing game: {e}")
            time.sleep(2)

    def network_loop(self):
        """Main network receive loop."""
        if not self.transport or not self.state_machine:
            print("[App] Network loop cannot start: transport or state machine missing")
            self.running = False
            return

        try:
            while self.running and self.state_machine.running:
                try:
                    # Receive incoming messages (non-blocking or with small timeout in implementation)
                    incoming = self.transport.receive()
                    if incoming:
                        # incoming is expected to be (bytes, (ip, port))
                        self.state_machine.handle_incoming(incoming)

                    # Tick reliability layer and state machine
                    # tick() lives in state_machine and will call reliability.tick
                    self.state_machine.tick()

                    # Small delay to prevent CPU spinning (reduced for faster handshake)
                    time.sleep(0.001)
                except Exception as e:
                    # Log but keep loop alive if possible
                    print(f"[App] Exception in network loop: {e}")
                    time.sleep(0.1)
        finally:
            print("[App] Network loop ended")
            self.running = False

    def input_loop(self):
        """Handle user input commands."""
        print("\n" + "="*60)
        print("POKEPROTOCOL BATTLE - COMMANDS:")
        print("="*60)
        print("  setup <pokemon> <sp_atk> <sp_def>  - Setup your Pokémon")
        print("  attack <move>                      - Use a move (your turn)")
        print("  chat <message>                     - Send chat message")
        print("  sticker <filepath>                 - Send sticker image")
        print("  list                               - List available Pokémon/moves")
        print("  status                             - Show battle status")
        print("  quit                               - Exit application")
        print("="*60 + "\n")

        # Defensive: ensure state_machine exists
        if not self.state_machine:
            print("[App] Error: state machine not initialized. Call setup() first.")
            return

        while self.running and self.state_machine.running:
            try:
                cmd = input("> ").strip()
                if not cmd:
                    continue

                parts = cmd.split(maxsplit=1)
                action = parts[0].lower()

                if action == "quit":
                    print("[App] Quitting...")
                    self.running = False
                    break

                elif action == "setup" and len(parts) > 1:
                    self._handle_setup(parts[1])

                elif action == "attack" and len(parts) > 1:
                    self._handle_attack(parts[1])

                elif action == "chat" and len(parts) > 1:
                    self._handle_chat(parts[1])

                elif action == "sticker" and len(parts) > 1:
                    self._handle_sticker(parts[1])

                elif action == "list":
                    self._handle_list()

                elif action == "status":
                    self._handle_status()

                else:
                    print("Unknown command. Type 'list' for Pokémon/moves or 'quit' to exit.")

            except EOFError:
                break
            except KeyboardInterrupt:
                print("\n[App] Interrupted")
                self.running = False
                break

    def _handle_setup(self, args: str):
        """Handle setup command."""
        parts = args.split()
        if len(parts) < 3:
            print("Usage: setup <pokemon_name> <special_attack_uses> <special_defense_uses>")
            return

        pokemon_name = parts[0]
        try:
            sp_atk_uses = int(parts[1])
            sp_def_uses = int(parts[2])
        except ValueError:
            print("Error: special_attack_uses and special_defense_uses must be integers")
            return

        # Create Pokémon
        pokemon = game_logic.create_pokemon(pokemon_name, sp_atk_uses, sp_def_uses)
        if not pokemon:
            print(f"Error: Unknown Pokémon '{pokemon_name}'. Type 'list' to see available Pokémon.")
            return

        # Send battle setup
        stat_boosts = {
            "special_attack_uses": sp_atk_uses,
            "special_defense_uses": sp_def_uses
        }

        # Ensure state_machine exists
        if not self.state_machine:
            print("[App] Error: State machine not initialized.")
            return

        self.state_machine.send_battle_setup(pokemon, stat_boosts)
        print(f"[App] Setup complete: {pokemon.name} (HP: {pokemon.hp})")

    def _handle_attack(self, move_name: str):
        """Handle attack command."""
        move_name = move_name.strip()

        # Check if move exists
        move = game_logic.get_move(move_name)
        if not move:
            print(f"Error: Unknown move '{move_name}'. Type 'list' to see available moves.")
            return

        if not self.state_machine:
            print("[App] Error: State machine not initialized.")
            return

        self.state_machine.send_attack(move_name)

    def _handle_chat(self, message: str):
        """Handle chat command."""
        if not self.state_machine:
            print("[App] Error: State machine not initialized.")
            return

        self.state_machine.send_chat_text(self.player_name, message)

    def _handle_sticker(self, filepath: str):
        """Handle sticker command."""
        try:
            with open(filepath, 'rb') as f:
                sticker_bytes = f.read()
            if not self.state_machine:
                print("[App] Error: State machine not initialized.")
                return
            self.state_machine.send_chat_sticker(self.player_name, sticker_bytes)
        except FileNotFoundError:
            print(f"Error: File '{filepath}' not found")
        except Exception as e:
            print(f"Error loading sticker: {e}")

    def _handle_list(self):
        """List available Pokémon and moves."""
        print("\n" + "="*60)
        print("AVAILABLE POKÉMON:")
        print("="*60)
        for name, data in game_logic.POKEMON_DB.items():
            types = "/".join(data['types'])
            print(f"  {name:12} - HP:{data['hp']:3} ATK:{data['attack']:3} "
                  f"SPATK:{data['special_attack']:3} DEF:{data['defense']:3} "
                  f"SPDEF:{data['special_defense']:3} [{types}]")

        print("\n" + "="*60)
        print("AVAILABLE MOVES:")
        print("="*60)
        for name, move in game_logic.MOVES_DB.items():
            print(f"  {name:15} - Power:{move.power:3} [{move.type:8}] ({move.damage_category})")
        print("="*60 + "\n")

    def _handle_status(self):
        """Show current battle status."""
        if not self.state_machine:
            print("[App] No active state machine")
            return

        print("\n" + "="*60)
        print("BATTLE STATUS:")
        print("="*60)
        print(f"Role: {self.state_machine.role}")
        print(f"State: {self.state_machine.state}")
        print(f"Turn Owner: {self.state_machine.turn_owner}")

        if self.state_machine.local_pokemon:
            p = self.state_machine.local_pokemon
            print(f"\nYour Pokémon: {p.name}")
            print(f"  HP: {p.hp}/{p.max_hp}")
            print(f"  Special Attack Boosts: {p.special_attack_uses}")
            print(f"  Special Defense Boosts: {p.special_defense_uses}")

        if self.state_machine.remote_pokemon:
            p = self.state_machine.remote_pokemon
            print(f"\nOpponent's Pokémon: {p.name}")
            print(f"  HP: {p.hp}/{p.max_hp}")

        print(f"\nSpectators: {len(self.state_machine.spectators)}")
        print("="*60 + "\n")

    def run(self):
        """Start the application."""
        if not self.state_machine:
            print("[App] Error: must call setup() before run().")
            return

        # Start network loop in separate thread
        self._network_thread = threading.Thread(target=self.network_loop, daemon=True)
        self._network_thread.start()

        # Start broadcast announcements for HOST
        if self.state_machine.role == "HOST" and self.broadcast:
            self._broadcast_thread = threading.Thread(target=self.announce_game_loop, daemon=True)
            self._broadcast_thread.start()

        # Run input loop in main thread
        try:
            self.input_loop()
        finally:
            # Cleanup
            self.cleanup()

    def cleanup(self):
        """Cleanup resources."""
        print("[App] Cleaning up...")
        self.running = False

        # Allow loops to notice running=False
        time.sleep(0.05)

        if self.transport:
            try:
                self.transport.close()
            except Exception as e:
                print(f"[App] Error closing transport: {e}")

        if self.broadcast:
            try:
                self.broadcast.close()
            except Exception as e:
                print(f"[App] Error closing broadcast: {e}")

        print("[App] Goodbye!")


def discover_games():
    """Discover available games on the network."""
    print("[Discovery] Searching for games on local network...")

    broadcast = BroadcastDiscovery(port=BROADCAST_PORT)
    try:
        broadcast.open()
    except Exception as e:
        print(f"[Discovery] Warning: broadcast discovery failed: {e}")
        return None

    games = broadcast.listen_for_games(timeout=3.0)

    broadcast.close()

    if not games:
        print("[Discovery] No games found.")
        return None

    print(f"\n[Discovery] Found {len(games)} game(s):")
    for i, (host_name, ip, port) in enumerate(games, 1):
        print(f"  {i}. {host_name} @ {ip}:{port}")

    try:
        choice_raw = input("Enter game number to join (0 to cancel): ").strip()
        choice = int(choice_raw)
        if 1 <= choice <= len(games):
            host_name, ip, port = games[choice - 1]
            print(f"[Discovery] Joining '{host_name}' at {ip}:{port}...")
            return ip, port  # Return (ip, port)
    except (ValueError, IndexError, EOFError):
        print("[Discovery] Invalid selection. Aborting join.")

    return None


def main():
    """Main entry point."""
    print("="*60)
    print(" POKEPROTOCOL - PEER-TO-PEER POKÉMON BATTLE")
    print("="*60)
    print("\nSelect mode:")
    print("  1. Host a game")
    print("  2. Join a game (discover)")
    print("  3. Join a game (manual IP)")
    print("  4. Spectate a game")
    print("  0. Exit")

    try:
        choice = input("\nChoice: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nExiting...")
        return

    app = BattleApplication()

    # Get player name
    try:
        name = input("Enter your name: ").strip()
        if name:
            app.player_name = name
    except (EOFError, KeyboardInterrupt):
        print("\nExiting...")
        return

    if choice == "1":
        # Host mode
        try:
            port = int(input("Enter port to host on (default 5555): ") or "5555")
        except ValueError:
            port = 5555

        print(f"\n[Host] Starting game on port {port}...")
        print("[Host] Waiting for opponent to join...")

        app.setup("HOST", port)
        app.run()

    elif choice == "2":
        # Join mode with discovery
        result = discover_games()
        if result:
            host_ip, host_port = result
            local_port = 5557  # Different port than host

            print(f"\n[Join] Connecting to {host_ip}:{host_port}...")
            app.setup("JOINER", local_port, host_ip, host_port)
            app.run()
        else:
            print("No game selected.")

    elif choice == "3":
        # Join mode with manual IP
        try:
            host_ip = input("Enter host IP address: ").strip()
            host_port = int(input("Enter host port: ").strip())
            local_port = int(input("Enter your local port (default 5557): ") or "5557")
        except (ValueError, EOFError, KeyboardInterrupt):
            print("Invalid input.")
            return

        print(f"\n[Join] Connecting to {host_ip}:{host_port}...")
        app.setup("JOINER", local_port, host_ip, host_port)
        app.run()

    elif choice == "4":
        # Spectator mode
        try:
            host_ip = input("Enter game IP address: ").strip()
            host_port = int(input("Enter game port: ").strip())
            local_port = int(input("Enter your local port (default 5558): ") or "5558")
        except (ValueError, EOFError, KeyboardInterrupt):
            print("Invalid input.")
            return

        print(f"\n[Spectate] Connecting to {host_ip}:{host_port}...")
        app.setup("SPECTATOR", local_port, host_ip, host_port)
        app.run()

    else:
        print("Exiting...")


if __name__ == "__main__":
    main()
