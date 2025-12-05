"""
protocol/reliability.py

Implements the custom reliability layer required by the PokeProtocol RFC:

- Monotonically increasing sequence numbers
- ACK messages for every incoming message with a sequence_number
- Pending message queue for un-ACKed messages
- Retransmission after timeout (default 500ms)
- Retry limit (default 3 attempts)

This module does NOT understand:
- game logic
- state machine transitions
- battle flow
- chat logic

It only ensures messages eventually arrive or the connection fails.
"""

import time
from typing import Dict, Tuple, Optional, Any, List

try:
    from .message import encode_message
except ImportError:
    from protocol.message import encode_message


class ReliabilityError(Exception):
    """Raised when reliability layer determines the connection is broken."""
    pass


class ReliabilityLayer:
    def __init__(self, timeout: float = 0.5, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries

        self._seq = 0  # local sequence counter
        # pending: (seq_number, addr) -> (msg_bytes, addr, last_sent_time, retries)
        self.pending: Dict[Tuple[int, Tuple[str, int]], Tuple[bytes, Tuple[str, int], float, int]] = {}

    # ------------------------------
    #  Sequence number management
    # ------------------------------

    def next_sequence_number(self) -> int:
        """Return the next sequence number and increment internal counter."""
        self._seq += 1
        return self._seq

    # ------------------------------
    #  Message Send with Reliability
    # ------------------------------

    def send_reliable(self, transport, fields: Dict[str, Any], addr: Tuple[str, int]):
        """
        Assign a sequence number, encode, send, and track message for retransmission.
        """
        seq = self.next_sequence_number()
        fields["sequence_number"] = seq
        msg_bytes = encode_message(fields)

        ok = transport.send(msg_bytes, addr)
        now = time.time()

        # Track for future retransmission
        self.pending[(seq, addr)] = (msg_bytes, addr, now, 0)
        return ok, seq

    def send_reliable_to_many(self, transport, fields: Dict[str, Any], addrs: List[Tuple[str, int]]):
        """
        Assign a single sequence number and send the same encoded message to multiple addresses.
        Tracks retransmissions per destination while preserving the same sequence_number.
        Returns (aggregate_ok, seq).
        """
        seq = self.next_sequence_number()
        fields["sequence_number"] = seq
        msg_bytes = encode_message(fields)
        now = time.time()
        aggregate_ok = True
        for addr in addrs:
            ok = transport.send(msg_bytes, addr)
            if not ok:
                aggregate_ok = False
            self.pending[(seq, addr)] = (msg_bytes, addr, now, 0)
        return aggregate_ok, seq

    def track_and_send_existing(self, transport, msg_bytes: bytes, seq: int, addrs: List[Tuple[str, int]]):
        """
        Send existing encoded message bytes (with an already-assigned sequence_number)
        to multiple addresses and track retransmissions per destination.
        """
        now = time.time()
        aggregate_ok = True
        for addr in addrs:
            ok = transport.send(msg_bytes, addr)
            if not ok:
                aggregate_ok = False
            self.pending[(seq, addr)] = (msg_bytes, addr, now, 0)
        return aggregate_ok

    # ------------------------------
    #  ACK Handling
    # ------------------------------

    def handle_ack(self, ack_number: int, addr: Tuple[str, int]):
        """
        Remove acknowledged messages for the given source from pending queue.
        """
        key = (ack_number, addr)
        if key in self.pending:
            del self.pending[key]

    def maybe_send_ack(self, transport, seq: Optional[int], addr: Tuple[str, int]):
        """
        If a message has a sequence_number, send back an ACK.
        """
        if seq is None:
            return
        ack_fields = {
            "message_type": "ACK",
            "ack_number": seq
        }
        ack_bytes = encode_message(ack_fields)
        transport.send(ack_bytes, addr)

    # ------------------------------
    #  Incoming Message Handling
    # ------------------------------

    def incoming_message(self, msg: Dict[str, str], addr: Tuple[str, int], transport):
        """
        Called by the state machine when ANY message arrives.
        Extract sequence_number if present and automatically ACK it.
        """
        seq = msg.get("sequence_number")
        if seq is not None:
            try:
                seq_int = int(seq)
            except ValueError:
                # Ignore malformed
                return
            # Align local sequence to remote for a shared, monotonically
            # increasing sequence space across both peers.
            # This ensures the next locally sent message uses seq+1,
            # making sender and receiver views consistent.
            if seq_int > self._seq:
                self._seq = seq_int

            self.maybe_send_ack(transport, seq_int, addr)

    # ------------------------------
    #  Retransmission Timer
    # ------------------------------

    def tick(self, transport):
        """
        Called every loop iteration from state_machine.
        Retransmits messages that have timed out.
        Raises ReliabilityError if retries exceed max_retries.
        """
        now = time.time()
        to_delete = []

        for key, (msg_bytes, addr, last, retries) in list(self.pending.items()):
            if now - last > self.timeout:
                if retries >= self.max_retries:
                    raise ReliabilityError(
                        f"Message seq {key[0]} to {addr} exceeded max retries ({self.max_retries}). "
                        "Peer appears unresponsive."
                    )

                # Retransmit
                transport.send(msg_bytes, addr)
                # Update tracking
                self.pending[key] = (msg_bytes, addr, now, retries + 1)

        # No deletion here; ACKs delete entries
