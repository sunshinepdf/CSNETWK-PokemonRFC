# File: network/waiting_for_move.py
"""WAITING_FOR_MOVE state handling for PokeProtocol"""

import threading
import time
from typing import Tuple, Optional, Callable, Dict
from enum import Enum


class WaitingForMoveState(Enum):
    """States within the WAITING_FOR_MOVE phase"""
    WAITING_FOR_TURN = "WAITING_FOR_TURN"
    WAITING_TO_ATTACK = "WAITING_TO_ATTACK"
    WAITING_FOR_DEFENSE = "WAITING_FOR_DEFENSE"
    READY_FOR_CALCULATION = "READY_FOR_CALCULATION"


class AttackAnnounceMessage:
    """Represents an ATTACK_ANNOUNCE message"""
    
    def __init__(self, move_name: str, sequence_number: int):
        self.message_type = "ATTACK_ANNOUNCE"
        self.move_name = move_name
        self.sequence_number = sequence_number
        
    def to_plaintext(self) -> str:
        """Convert to plain-text format for UDP"""
        return "\n".join([
            f"message_type: {self.message_type}",
            f"move_name: {self.move_name}",
            f"sequence_number: {self.sequence_number}"
        ])


class DefenseAnnounceMessage:
    """Represents a DEFENSE_ANNOUNCE message"""
    
    def __init__(self, sequence_number: int):
        self.message_type = "DEFENSE_ANNOUNCE"
        self.sequence_number = sequence_number
        
    def to_plaintext(self) -> str:
        """Convert to plain-text format for UDP"""
        return "\n".join([
            f"message_type: {self.message_type}",
            f"sequence_number: {self.sequence_number}"
        ])


class WaitingForMoveHandler:
    """
    Handles the WAITING_FOR_MOVE state as per PokeProtocol RFC:
    1. Host peer goes first
    2. Acting peer sends ATTACK_ANNOUNCE
    3. Defending peer sends DEFENSE_ANNOUNCE
    4. Both peers transition to PROCESSING_TURN
    """
    
    def __init__(self, udp_transport, verbose: bool = False,
                 on_turn_ready: Optional[Callable[[str, str], None]] = None):
        """
        Initialize the WAITING_FOR_MOVE handler
        
        Args:
            udp_transport: UDPTransport instance for communication
            verbose: Enable verbose logging
            on_turn_ready: Callback when both ATTACK_ANNOUNCE and DEFENSE_ANNOUNCE are complete
                           Parameters: (move_name, opponent_address)
        """
        self.udp = udp_transport
        self.verbose = verbose
        self.on_turn_ready = on_turn_ready
        
        # State management
        self.running = False
        self.current_state = WaitingForMoveState.WAITING_FOR_TURN
        self.is_my_turn = False  # True if it's this peer's turn to attack
        self.opponent_address = None
        self.move_name = None
        
        # Reliability tracking
        self.pending_acks = {}  # sequence_number -> (message_text, addr, retry_count, timestamp)
        self.ack_received = {}  # sequence_number -> bool
        self.ack_timeout = 0.5  # 500ms as per RFC
        self.max_retries = 3  # as per RFC
        
        # Message tracking
        self.last_attack_announce = None  # (move_name, sequence_number, from_addr)
        self.last_defense_announce = None  # (sequence_number, from_addr)
        
        # Thread safety
        self.lock = threading.Lock()
        
        # Sequence numbers for outgoing messages
        self.sequence_number = 0
    
    def start(self, is_host: bool, opponent_address: Tuple[str, int]):
        """
        Start the WAITING_FOR_MOVE phase
        
        Args:
            is_host: True if this peer is the host (goes first)
            opponent_address: Address of the opponent
        """
        with self.lock:
            self.running = True
            self.opponent_address = opponent_address
            self.current_state = WaitingForMoveState.WAITING_FOR_TURN
            self.is_my_turn = is_host  # Host goes first
            
            if self.is_my_turn:
                if self.verbose:
                    print(f"[WAITING_FOR_MOVE] It's my turn to attack (Host)")
                self.current_state = WaitingForMoveState.WAITING_TO_ATTACK
            else:
                if self.verbose:
                    print(f"[WAITING_FOR_MOVE] Waiting for opponent's ATTACK_ANNOUNCE")
                self.current_state = WaitingForMoveState.WAITING_FOR_DEFENSE
            
            # Start monitoring threads
            threading.Thread(target=self._receive_loop, daemon=True).start()
            threading.Thread(target=self._ack_retransmission_loop, daemon=True).start()
    
    def _get_next_sequence(self) -> int:
        """Get the next sequence number"""
        with self.lock:
            self.sequence_number += 1
            return self.sequence_number
    
    def send_attack_announce(self, move_name: str) -> bool:
        """
        Send an ATTACK_ANNOUNCE message (called when it's our turn)
        
        Args:
            move_name: Name of the move to use
            
        Returns:
            True if message was sent successfully
        """
        with self.lock:
            if not self.is_my_turn:
                if self.verbose:
                    print("[WAITING_FOR_MOVE] Not my turn to attack")
                return False
            
            if self.current_state != WaitingForMoveState.WAITING_TO_ATTACK:
                if self.verbose:
                    print(f"[WAITING_FOR_MOVE] Not in correct state for attack: {self.current_state}")
                return False
        
        seq = self._get_next_sequence()
        msg = AttackAnnounceMessage(move_name, seq)
        msg_text = msg.to_plaintext()
        
        if self.verbose:
            print(f"[WAITING_FOR_MOVE] Sending ATTACK_ANNOUNCE: {move_name} (seq={seq})")
        
        success = self.udp.send(msg_text.encode(), self.opponent_address)
        
        if success:
            with self.lock:
                self.move_name = move_name
                self.pending_acks[seq] = (msg_text, self.opponent_address, 0, time.time())
                self.ack_received[seq] = False
                self.current_state = WaitingForMoveState.WAITING_FOR_DEFENSE
                
                if self.verbose:
                    print(f"[WAITING_FOR_MOVE] Waiting for DEFENSE_ANNOUNCE from opponent")
            
            return True
        return False
    
    def _ack_retransmission_loop(self):
        """Handle retransmission of messages if no ACK received"""
        while self.running:
            current_time = time.time()
            
            with self.lock:
                to_remove = []
                
                for seq, (msg_text, addr, retry_count, timestamp) in list(self.pending_acks.items()):
                    if self.ack_received.get(seq, False):
                        to_remove.append(seq)
                        if self.verbose:
                            print(f"[WAITING_FOR_MOVE] ACK confirmed for message {seq}")
                        continue
                    
                    if current_time - timestamp > self.ack_timeout:
                        if retry_count < self.max_retries:
                            new_retry_count = retry_count + 1
                            self.pending_acks[seq] = (msg_text, addr, new_retry_count, current_time)
                            self.udp.send(msg_text.encode(), addr)
                            
                            if self.verbose:
                                print(f"[WAITING_FOR_MOVE] Retransmitting message {seq} (attempt {new_retry_count})")
                        else:
                            to_remove.append(seq)
                            print(f"[WAITING_FOR_MOVE] Max retries reached for message {seq}. Connection may be lost.")
                
                for seq in to_remove:
                    self.pending_acks.pop(seq, None)
                    self.ack_received.pop(seq, None)
            
            time.sleep(0.05)
    
    def _receive_loop(self):
        """Main receive loop for handling incoming messages"""
        while self.running:
            packet = self.udp.receive()
            if packet:
                data, addr = packet
                self._handle_incoming(data, addr)
            time.sleep(0.01)
    
    def _handle_incoming(self, data: bytes, addr: Tuple[str, int]):
        """Handle incoming UDP packet"""
        try:
            text = data.decode()
            msg = self._parse_message(text)
            message_type = msg.get("message_type")
            
            if message_type == "ATTACK_ANNOUNCE":
                self._handle_attack_announce(msg, addr)
            elif message_type == "DEFENSE_ANNOUNCE":
                self._handle_defense_announce(msg, addr)
            elif message_type == "ACK":
                self._handle_ack(msg, addr)
            
        except Exception as e:
            if self.verbose:
                print(f"[WAITING_FOR_MOVE] Failed to parse message from {addr}: {e}")
    
    def _parse_message(self, data: str) -> Dict:
        """Parse newline-separated key:value into dictionary"""
        msg = {}
        for line in data.strip().split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                msg[key.strip()] = value.strip()
        return msg
    
    def _handle_attack_announce(self, msg: Dict, addr: Tuple[str, int]):
        """Handle an ATTACK_ANNOUNCE message from opponent"""
        move_name = msg.get("move_name", "")
        seq = msg.get("sequence_number", "0")
        
        with self.lock:
            # Check if we're expecting this
            if not self.running:
                return
            
            if self.current_state != WaitingForMoveState.WAITING_FOR_DEFENSE:
                if self.verbose:
                    print(f"[WAITING_FOR_MOVE] Unexpected ATTACK_ANNOUNCE in state: {self.current_state}")
                return
            
            if addr != self.opponent_address:
                if self.verbose:
                    print(f"[WAITING_FOR_MOVE] ATTACK_ANNOUNCE from unexpected address: {addr}")
                return
        
        if self.verbose:
            print(f"[WAITING_FOR_MOVE] Received ATTACK_ANNOUNCE: {move_name} (seq={seq})")
        
        # Send ACK
        ack_msg = f"message_type: ACK\nack_number: {seq}"
        self.udp.send(ack_msg.encode(), addr)
        
        # Send DEFENSE_ANNOUNCE as per RFC
        defense_seq = self._get_next_sequence()
        defense_msg = DefenseAnnounceMessage(defense_seq)
        defense_text = defense_msg.to_plaintext()
        
        if self.verbose:
            print(f"[WAITING_FOR_MOVE] Sending DEFENSE_ANNOUNCE (seq={defense_seq})")
        
        success = self.udp.send(defense_text.encode(), addr)
        
        if success:
            with self.lock:
                self.last_attack_announce = (move_name, int(seq), addr)
                self.pending_acks[defense_seq] = (defense_text, addr, 0, time.time())
                self.ack_received[defense_seq] = False
                self.current_state = WaitingForMoveState.READY_FOR_CALCULATION
                self.move_name = move_name
                
                if self.verbose:
                    print(f"[WAITING_FOR_MOVE] Ready for calculation phase")
        
        # Store attack announce for later reference
        with self.lock:
            self.last_attack_announce = (move_name, int(seq), addr)
    
    def _handle_defense_announce(self, msg: Dict, addr: Tuple[str, int]):
        """Handle a DEFENSE_ANNOUNCE message from opponent"""
        seq = msg.get("sequence_number", "0")
        
        with self.lock:
            # Check if we're expecting this
            if not self.running:
                return
            
            if self.current_state != WaitingForMoveState.WAITING_FOR_DEFENSE:
                if self.verbose:
                    print(f"[WAITING_FOR_MOVE] Unexpected DEFENSE_ANNOUNCE in state: {self.current_state}")
                return
            
            if addr != self.opponent_address:
                if self.verbose:
                    print(f"[WAITING_FOR_MOVE] DEFENSE_ANNOUNCE from unexpected address: {addr}")
                return
        
        if self.verbose:
            print(f"[WAITING_FOR_MOVE] Received DEFENSE_ANNOUNCE (seq={seq})")
        
        # Send ACK
        ack_msg = f"message_type: ACK\nack_number: {seq}"
        self.udp.send(ack_msg.encode(), addr)
        
        with self.lock:
            self.last_defense_announce = (int(seq), addr)
            self.current_state = WaitingForMoveState.READY_FOR_CALCULATION
            
            if self.verbose:
                print(f"[WAITING_FOR_MOVE] Ready for calculation phase")
            
            # Trigger callback if both messages are complete
            if self.move_name and self.on_turn_ready:
                self.on_turn_ready(self.move_name, self.opponent_address)
    
    def _handle_ack(self, msg: Dict, addr: Tuple[str, int]):
        """Handle ACK message"""
        ack_num = msg.get("ack_number")
        if ack_num:
            seq = int(ack_num)
            with self.lock:
                if seq in self.ack_received:
                    self.ack_received[seq] = True
                    if self.verbose:
                        print(f"[WAITING_FOR_MOVE] ACK received for message {seq}")
    
    def is_ready_for_calculation(self) -> bool:
        """Check if ready to transition to PROCESSING_TURN state"""
        with self.lock:
            return self.current_state == WaitingForMoveState.READY_FOR_CALCULATION
    
    def get_move_info(self) -> Tuple[Optional[str], Optional[Tuple[str, int]]]:
        """Get the move name and opponent address for calculation"""
        with self.lock:
            return self.move_name, self.opponent_address
    
    def prepare_for_next_turn(self):
        """Reset state for next turn (called after PROCESSING_TURN completes)"""
        with self.lock:
            if self.running:
                # Switch turns
                self.is_my_turn = not self.is_my_turn
                
                if self.is_my_turn:
                    self.current_state = WaitingForMoveState.WAITING_TO_ATTACK
                    if self.verbose:
                        print(f"[WAITING_FOR_MOVE] It's now my turn to attack")
                else:
                    self.current_state = WaitingForMoveState.WAITING_FOR_DEFENSE
                    if self.verbose:
                        print(f"[WAITING_FOR_MOVE] Waiting for opponent's ATTACK_ANNOUNCE")
                
                # Clear move name for next turn
                self.move_name = None
                
                # Clear message tracking
                self.last_attack_announce = None
                self.last_defense_announce = None
    
    def stop(self):
        """Stop the WAITING_FOR_MOVE handler"""
        with self.lock:
            self.running = False
            self.current_state = WaitingForMoveState.WAITING_FOR_TURN
            self.is_my_turn = False
            self.opponent_address = None
            self.move_name = None
            self.pending_acks.clear()
            self.ack_received.clear()
            self.last_attack_announce = None
            self.last_defense_announce = None
        
        if self.verbose:
            print("[WAITING_FOR_MOVE] Handler stopped")


# Helper functions for message creation
def create_attack_announce(move_name: str, sequence_number: int = 1) -> str:
    """Create an ATTACK_ANNOUNCE message"""
    msg = AttackAnnounceMessage(move_name, sequence_number)
    return msg.to_plaintext()


def create_defense_announce(sequence_number: int = 1) -> str:
    """Create a DEFENSE_ANNOUNCE message"""
    msg = DefenseAnnounceMessage(sequence_number)
    return msg.to_plaintext()