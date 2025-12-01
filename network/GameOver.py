# File: network/game_over.py
"""Game Over message handling for PokeProtocol"""

import threading
import time
from typing import Tuple, Optional, Callable


class GameOverMessage:
    """Represents a GAME_OVER message"""

    def __init__(self, winner: str, loser: str, sequence_number: int):
        self.message_type = "GAME_OVER"
        self.winner = winner
        self.loser = loser
        self.sequence_number = sequence_number

    def to_plaintext(self) -> str:
        """Convert the message to a plain-text, newline-separated format for UDP."""
        return "\n".join([
            f"message_type: {self.message_type}",
            f"winner: {self.winner}",
            f"loser: {self.loser}",
            f"sequence_number: {self.sequence_number}"
        ])


class GameOverHandler:
    """Handles sending & receiving GAME_OVER messages"""

    def __init__(self, udp_transport, verbose: bool = False,
                 battle_end_callback: Optional[Callable[[str, str], None]] = None):
        self.udp = udp_transport
        self.verbose = verbose
        self.battle_end_callback = battle_end_callback
        self.sequence_number = 0
        self.running = False

        # Reliability tracking
        self.pending_acks = {}
        self.ack_received = {}
        self.ack_timeout = 0.5
        self.max_retries = 3

        # State flags
        self.game_over_sent = False
        self.game_over_received = False

        # Thread safety
        self.lock = threading.Lock()

    def start(self):
        """Start listening for incoming GAME_OVER messages"""
        self.running = True
        threading.Thread(target=self._retransmission_loop, daemon=True).start()

    def _retransmission_loop(self):
        """Check for pending ACKs and retransmit if necessary"""
        while self.running:
            current_time = time.time()

            with self.lock:
                to_remove = []

                for seq, (msg_text, addr, retry_count, timestamp) in list(self.pending_acks.items()):
                    if self.ack_received.get(seq, False):
                        to_remove.append(seq)
                        if self.verbose:
                            print(f"[GAME_OVER] ACK confirmed for message {seq}")
                        continue

                    if current_time - timestamp > self.ack_timeout:
                        if retry_count < self.max_retries:
                            new_retry_count = retry_count + 1
                            self.pending_acks[seq] = (msg_text, addr, new_retry_count, current_time)
                            self.udp.send(msg_text.encode(), addr)
                            if self.verbose:
                                print(f"[GAME_OVER] Retransmitting message {seq} (attempt {new_retry_count})")
                        else:
                            to_remove.append(seq)
                            print(f"[GAME_OVER] Max retries reached for message {seq}. Connection may be lost.")

                for seq in to_remove:
                    self.pending_acks.pop(seq, None)
                    self.ack_received.pop(seq, None)

            time.sleep(0.05)

    def _get_next_sequence(self) -> int:
        """Get the next sequence number"""
        with self.lock:
            self.sequence_number += 1
            return self.sequence_number

    def send_game_over(self, recipient: Tuple[str, int], winner: str, loser: str):
        """Send a GAME_OVER message"""
        with self.lock:
            if self.game_over_sent:
                if self.verbose:
                    print("[GAME_OVER] GAME_OVER already sent, ignoring duplicate send request")
                return None
            self.game_over_sent = True

        seq = self._get_next_sequence()
        msg = GameOverMessage(winner, loser, seq)
        msg_text = msg.to_plaintext()

        if self.verbose:
            print(f"[GAME_OVER] Sending: {winner} defeated {loser} (seq={seq})")

        self.udp.send(msg_text.encode(), recipient)

        with self.lock:
            self.pending_acks[seq] = (msg_text, recipient, 0, time.time())
            self.ack_received[seq] = False

        return seq

    def parse_message(self, data: str) -> dict:
        """Parse newline-separated key:value into a dictionary"""
        msg = {}
        for line in data.strip().split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                msg[key.strip()] = value.strip()
        return msg

    def handle_message(self, data: bytes, addr: Tuple[str, int]):
        """Handle an incoming message"""
        try:
            text = data.decode()
            msg = self.parse_message(text)
            message_type = msg.get("message_type")

            if message_type == "GAME_OVER":
                winner = msg.get("winner", "Unknown")
                loser = msg.get("loser", "Unknown")
                seq = msg.get("sequence_number", "0")

                if self.verbose:
                    print(f"[GAME_OVER] Received: {winner} defeated {loser} (seq={seq})")

                ack_msg = f"message_type: ACK\nack_number: {seq}"
                self.udp.send(ack_msg.encode(), addr)

                with self.lock:
                    if not self.game_over_received:
                        self.game_over_received = True
                        if self.battle_end_callback:
                            self.battle_end_callback(winner, loser)

                return GameOverMessage(winner, loser, int(seq))

            elif message_type == "ACK":
                ack_num = msg.get("ack_number")
                if ack_num:
                    seq = int(ack_num)
                    with self.lock:
                        if seq in self.ack_received:
                            self.ack_received[seq] = True
                            if self.verbose:
                                print(f"[GAME_OVER] ACK received for message {seq}")

        except Exception as e:
            print(f"[GAME_OVER] Failed to parse message: {e}")

        return None

    def check_hp_and_send_game_over(self, opponent_hp: int, opponent_name: str,
                                    my_hp: int, my_name: str,
                                    opponent_addr: Tuple[str, int]) -> bool:
        """
        Check if opponent's HP is zero or below and send GAME_OVER if so
        Also handles edge case where both Pokemon faint simultaneously

        Returns:
            True if GAME_OVER was sent, False otherwise
        """
        if opponent_hp <= 0 and my_hp <= 0:
            if self.verbose:
                print(f"[GAME_OVER] Both Pokemon fainted! It's a draw!")
            self.send_game_over(opponent_addr, my_name, opponent_name)
            return True

        elif opponent_hp <= 0:
            if self.verbose:
                print(f"[GAME_OVER] {opponent_name} has fainted! Sending GAME_OVER...")
            self.send_game_over(opponent_addr, my_name, opponent_name)
            return True

        elif my_hp <= 0:
            if self.verbose:
                print(f"[GAME_OVER] {my_name} has fainted! Waiting for opponent's GAME_OVER message...")
            return False

        return False

    def stop(self):
        """Stop the handler"""
        self.running = False
        with self.lock:
            self.pending_acks.clear()
            self.ack_received.clear()
            self.game_over_sent = False
            self.game_over_received = False