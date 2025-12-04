"""
protocol/chat.py

Chat helper module — works WITH the state machine, not independently.

This module:
- Builds chat message dictionaries
- Encodes stickers into Base64
- Saves received stickers to files
- Provides clean helper functions for TEXT and STICKER chat

This module DOES NOT:
- send UDP packets directly
- run its own threads
- perform ACK handling
- parse messages
- listen for messages

Incoming chat messages are handled in state_machine._on_chat()
Outgoing chat messages are sent through ReliabilityLayer.send_reliable()
"""

import base64
from typing import Dict


def make_text_message(sender_name: str, text: str) -> Dict:
    """
    Build the RFC-compliant CHAT_MESSAGE dictionary for TEXT.
    The state machine will attach a sequence_number.
    """
    return {
        "message_type": "CHAT_MESSAGE",
        "sender_name": sender_name,
        "content_type": "TEXT",
        "message_text": text
    }


def make_sticker_message(sender_name: str, raw_bytes: bytes) -> Dict:
    """
    Build the RFC-compliant CHAT_MESSAGE dictionary for STICKER.
    Converts binary sticker data into Base64 string.
    """
    sticker_data = base64.b64encode(raw_bytes).decode("utf-8")
    return {
        "message_type": "CHAT_MESSAGE",
        "sender_name": sender_name,
        "content_type": "STICKER",
        "sticker_data": sticker_data
    }


def save_sticker_to_file(sticker_data: str, filename: str = "received_sticker.png"):
    """
    Saves a Base64 sticker to a PNG file.
    Called by state_machine when receiving sticker chat.
    """
    raw = base64.b64decode(sticker_data)
    with open(filename, "wb") as f:
        f.write(raw)
    return filename
