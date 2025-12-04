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
import io
from typing import Dict, Tuple
from PIL import Image


class StickerValidationError(Exception):
    """Raised when sticker doesn't meet RFC requirements."""
    pass


def validate_sticker(raw_bytes: bytes) -> Tuple[bool, str]:
    """
    Validate sticker meets RFC requirements:
    - Must be 320px x 320px
    - Must be under 10MB
    
    Returns (is_valid, error_message)
    """
    # Check size
    if len(raw_bytes) > 10 * 1024 * 1024:  # 10MB
        return False, f"Sticker too large: {len(raw_bytes)} bytes (max 10MB)"
    
    try:
        # Check dimensions
        img = Image.open(io.BytesIO(raw_bytes))
        if img.size != (320, 320):
            return False, f"Sticker must be 320x320px, got {img.size[0]}x{img.size[1]}px"
    except Exception as e:
        return False, f"Invalid image format: {e}"
    
    return True, ""


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
    Validates sticker before encoding.
    
    Raises StickerValidationError if sticker doesn't meet requirements.
    """
    # Validate sticker
    is_valid, error_msg = validate_sticker(raw_bytes)
    if not is_valid:
        raise StickerValidationError(error_msg)
    
    sticker_data = base64.b64encode(raw_bytes).decode("utf-8")
    
    # RFC warns about messages over 1.5KB (IP fragmentation)
    estimated_size = len(sticker_data) + 200  # overhead for message fields
    if estimated_size > 1500:
        print(f"[CHAT WARNING] Sticker message is {estimated_size} bytes, may cause IP fragmentation")
    
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