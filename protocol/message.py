"""
protocol/messages.py

Simple RFC-style message serialization/parsing for PokeProtocol.

Message format (one message per UDP datagram):
key: value
key: value
...

All values are treated as strings. Use helpers to convert fields like
sequence_number to/from integers where needed.
"""

from typing import Dict, List, Tuple, Optional


class MessageParseError(ValueError):
    pass


def encode_message(fields: Dict[str, str]) -> bytes:
    """
    Encode a mapping of key -> value into the RFC plain-text format.

    - Preserves insertion order (use OrderedDict if ordering matters).
    - Converts values to string.
    - Returns bytes (utf-8).
    """
    if not isinstance(fields, dict):
        raise TypeError("fields must be a dict")
    lines = []
    for k, v in fields.items():
        if ":" in k:
            raise ValueError("message keys must not contain ':'")
        # Convert to string; strip trailing newline characters to avoid injection
        val = "" if v is None else str(v).replace("\n", " ")
        lines.append(f"{k}: {val}")
    text = "\n".join(lines)
    return text.encode("utf-8")


def decode_message(data: bytes) -> Dict[str, str]:
    """
    Decode bytes (utf-8) into a dict of key -> value.
    Raises MessageParseError on malformed lines.
    """
    try:
        text = data.decode("utf-8")
    except Exception as e:
        raise MessageParseError(f"Unable to decode bytes: {e}")

    msg: Dict[str, str] = {}
    if text == "":
        return msg

    for lineno, raw in enumerate(text.splitlines(), start=1):
        # Allow blank lines (skip)
        if not raw.strip():
            continue
        if ": " not in raw:
            # Be permissive: also accept "key:value" (no space) but warn via exception.
            if ":" in raw:
                k, v = raw.split(":", 1)
                msg[k.strip()] = v.strip()
            else:
                raise MessageParseError(f"Malformed line {lineno}: '{raw}' (expected 'key: value')")
        else:
            k, v = raw.split(": ", 1)
            msg[k.strip()] = v.strip()
    return msg


def require_fields(msg: Dict[str, str], required: List[str]) -> Tuple[bool, Optional[str]]:
    """
    Ensure `msg` contains all keys in `required`.
    Returns (True, None) if OK, otherwise (False, missing_field_name).
    """
    for f in required:
        if f not in msg:
            return False, f
    return True, None


def parse_int_field(msg: Dict[str, str], field: str, default: Optional[int] = None) -> Optional[int]:
    """
    Parse integer field safely. Returns an integer or default.
    Raises MessageParseError if present but not an integer.
    """
    if field not in msg:
        return default
    raw = msg[field].strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise MessageParseError(f"Field '{field}' is not a valid integer: '{raw}'")


# Convenience factories for common messages (small helpers)
def mk_handshake_response(seed: int) -> bytes:
    return encode_message({"message_type": "HANDSHAKE_RESPONSE", "seed": str(seed)})


def mk_ack(ack_number: int) -> bytes:
    return encode_message({"message_type": "ACK", "ack_number": str(ack_number)})


def mk_chat_text(sender_name: str, message_text: str, sequence_number: Optional[int] = None) -> bytes:
    fields = {
        "message_type": "CHAT_MESSAGE",
        "sender_name": sender_name,
        "content_type": "TEXT",
        "message_text": message_text,
    }
    if sequence_number is not None:
        fields["sequence_number"] = str(sequence_number)
    return encode_message(fields)