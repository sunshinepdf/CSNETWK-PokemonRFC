# File: protocol/game_over.py
"""Game Over message handling for PokeProtocol"""

from typing import Optional, Callable
from protocol.message import Message


class GameOverHandler:
    """Handles GAME_OVER logic and GAME_OVER message creation"""

    def __init__(self, verbose: bool = False,
                 battle_end_callback: Optional[Callable[[str, str], None]] = None):
        """
        Initialize GameOverHandler
        
        Args:
            verbose: Enable verbose logging
            battle_end_callback: Callback when game ends (winner, loser)
        """
        
        self.verbose = verbose
        self.battle_end_callback = battle_end_callback

        # State flags
        self.game_over_sent = False
        self.game_over_received = False


    def create_game_over_message(self, winner: str, loser: str, sequence_number: Optional[int] = None) -> Optional[Message]:
        """
        Create a GAME_OVER message
        
        Args:
            winner: Name of winning Pokemon
            loser: Name of losing Pokemon
            
        Returns:
            Protocol Message object
        """
        if self.game_over_sent:
            if self.verbose:
                print("[GAME_OVER] GAME_OVER already sent, ignoring duplicate")
            return None
        
        self.game_over_sent = True
        
        # Prepare message fields
        msg_fields = {
            'winner': winner,
            'loser': loser
        }

        if sequence_number is not None:
            msg_fields['sequence_number'] = sequence_number

        msg = Message('GAME_OVER', **msg_fields)

        if self.verbose:
            # Print the serialized message as multiple lines, matching wire format
            try:
                serialized = msg.serialize().decode('utf-8')
            except Exception:
                serialized = f"message_type: GAME_OVER\nwinner: {winner}\nloser: {loser}"

            print(serialized)

            # If there's no sequence number, explicitly show N/A to match desired format
            if 'sequence_number' not in msg.fields:
                print('sequence_number: N/A')

        return msg

    def handle_incoming_message(self, message: Message, addr: tuple):
        """
        Handle incoming GAME_OVER message
        
        Args:
            message: Protocol Message object
            addr: Sender address
        """
        winner = message.fields.get("winner", "Unknown")
        loser = message.fields.get("loser", "Unknown")
        
        if self.verbose:
            print(f"[GAME_OVER] Received: {winner} defeated {loser}")
        
        if not self.game_over_received:
            self.game_over_received = True
            
            if self.battle_end_callback:
                self.battle_end_callback(winner, loser)

    def check_hp_and_create_game_over(self, opponent_hp: int, opponent_name: str,
                                      my_hp: int, my_name: str) -> Optional[Message]:
        """
        Check if game is over and create GAME_OVER message if needed
        
        Args:
            opponent_hp: Opponent's current HP
            opponent_name: Opponent's Pokemon name
            my_hp: My current HP
            my_name: My Pokemon name
            
        Returns:
            GAME_OVER Message if game ended, None otherwise
        """
        # Both fainted (draw)
        if opponent_hp <= 0 and my_hp <= 0:
            if self.verbose:
                print(f"[GAME_OVER] Both Pokemon fainted! It's a draw!")
            return self.create_game_over_message(my_name, opponent_name)
        
        # Opponent fainted (I win)
        elif opponent_hp <= 0:
            if self.verbose:
                print(f"[GAME_OVER] {opponent_name} has fainted! I win!")
            return self.create_game_over_message(my_name, opponent_name)
        
        # I fainted (opponent wins)
        elif my_hp <= 0:
            if self.verbose:
                print(f"[GAME_OVER] {my_name} has fainted! Waiting for opponent's GAME_OVER...")
            return None  # Wait for opponent to send GAME_OVER
        
        return None

    def reset(self):
        """Reset handler for new game"""
        self.game_over_sent = False
        self.game_over_received = False
        
        if self.verbose:
            print("[GAME_OVER] Handler reset")