import threading
from typing import Tuple, Optional, Callable
import base64

class ChatMessage:
    """Represents a chat message (TEXT or STICKER)"""

    def __init__(self, sender_name: str, sequence_number: int,
                 content_type: str = "TEXT", message_text: str = None, sticker_data: str = None):
        self.message_type = "CHAT_MESSAGE"
        self.sender_name = sender_name
        self.content_type = content_type
        self.message_text = message_text
        self.sticker_data = sticker_data
        self.sequence_number = sequence_number

        # Validation
        if self.content_type not in ["TEXT", "STICKER"]:
            raise ValueError("content_type must be 'TEXT' or 'STICKER'")
        if self.content_type == "TEXT" and not self.message_text:
            raise ValueError("message_text must be provided for TEXT messages")
        if self.content_type == "STICKER" and not self.sticker_data:
            raise ValueError("sticker_data must be provided for STICKER messages")

    def to_plaintext(self) -> str:
        """Convert the message to a plain-text, newline-separated format for UDP."""
        lines = [
            f"message_type: {self.message_type}",
            f"sender_name: {self.sender_name}",
            f"content_type: {self.content_type}",
            f"sequence_number: {self.sequence_number}"
        ]
        if self.content_type == "TEXT":
            lines.append(f"message_text: {self.message_text}")
        elif self.content_type == "STICKER":
            lines.append(f"sticker_data: {self.sticker_data}")
        return "\n".join(lines)


class ChatHandler:
    """Handles sending & receiving chat messages independently from battle logic"""

    def __init__(self, udp_transport, display_callback: Optional[Callable[[str], None]] = None):
        self.udp = udp_transport
        self.display_callback = display_callback or print
        self.sequence_number = 0
        self.running = False

    def start(self):
        """Start listening for incoming chat messages"""
        self.running = True
        threading.Thread(target=self.listen_loop, daemon=True).start()

    def listen_loop(self):
        while self.running:
            packet = self.udp.receive()
            if packet:
                data, addr = packet
                try:
                    msg = self.parse_message(data.decode())
                    self.handle_message(msg, addr)
                except Exception as e:
                    print(f"[CHAT] Failed to parse message: {e}")

    def parse_message(self, data: str) -> dict:
        """Parse newline-separated key:value into a dictionary"""
        msg = {}
        for line in data.strip().split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                msg[key.strip()] = value.strip()
        return msg

    def handle_message(self, msg: dict, addr: Tuple[str, int]):
        message_type = msg.get("message_type")

        if message_type == "CHAT_MESSAGE":
            sender = msg.get("sender_name", "Unknown")
            content_type = msg.get("content_type", "TEXT")
            seq = msg.get("sequence_number")
            if content_type == "TEXT":
                text = msg.get("message_text", "")
                self.display_callback(f"[CHAT] {sender}: {text}")
            elif content_type == "STICKER":
                self.display_callback(f"[CHAT] {sender} sent a sticker (Base64 length={len(msg.get('sticker_data',''))})")

            ack_msg = f"message_type: CHAT_ACK\nsequence_number: {seq}"
            self.udp.send(ack_msg.encode(), addr)

        elif message_type == "CHAT_ACK":
            seq = msg.get("sequence_number")
            print(f"[CHAT] ACK received for message {seq}")

    def send_text(self, recipient: Tuple[str, int], sender_name: str, text: str):
        """Send a TEXT message"""
        self.sequence_number += 1
        msg = ChatMessage(sender_name, self.sequence_number, content_type="TEXT", message_text=text)
        self.udp.send(msg.to_plaintext().encode(), recipient)

    def send_sticker(self, recipient: Tuple[str, int], sender_name: str, sticker_bytes: bytes):
        """Send a STICKER message as Base64"""
        self.sequence_number += 1
        b64_data = base64.b64encode(sticker_bytes).decode("utf-8")
        msg = ChatMessage(sender_name, self.sequence_number, content_type="STICKER", sticker_data=b64_data)
        self.udp.send(msg.to_plaintext().encode(), recipient)
