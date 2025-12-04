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
from typing import Dict, Tuple, Optional, Any

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
        # pending: seq_number -> (msg_bytes, addr, last_sent_time, retries)
        self.pending: Dict[int, Tuple[bytes, Tuple[str, int], float, int]] = {}

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
        self.pending[seq] = (msg_bytes, addr, now, 0)
        return ok, seq

    # ------------------------------
    #  ACK Handling
    # ------------------------------

    def handle_ack(self, ack_number: int):
        """
        Remove acknowledged messages from pending queue.
        """
        if ack_number in self.pending:
            del self.pending[ack_number]

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

        for seq, (msg_bytes, addr, last, retries) in self.pending.items():
            if now - last > self.timeout:
                if retries >= self.max_retries:
                    raise ReliabilityError(
                        f"Message seq {seq} exceeded max retries ({self.max_retries}). "
                        "Peer appears unresponsive."
                    )

                # Retransmit
                transport.send(msg_bytes, addr)
                # Update tracking
                self.pending[seq] = (msg_bytes, addr, now, retries + 1)

        # No deletion here; ACKs delete entries
