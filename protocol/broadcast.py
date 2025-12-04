"""
protocol/broadcast.py

UDP broadcast-based game discovery for PokeProtocol.
"""

import socket
from typing import Optional, Tuple, List
from .message import encode_message, decode_message

class BroadcastDiscovery:
    """Handles UDP broadcast for game discovery on local network."""

    def __init__(self, port: int = 5556):
        """
        Args:
            port: UDP broadcast port for announcements and discovery.
        """
        self.port = port
        self.socket: Optional[socket.socket] = None

    def open(self) -> None:
        """Open and configure the broadcast socket."""
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Allow reuse on all platforms
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Allow multiple listeners on Linux/macOS
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass

        # Enable broadcast sending
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        # Bind ONCE here. No rebinding in listen_for_games().
        try:
            self.socket.bind(("", self.port))
        except OSError as e:
            print(f"[Broadcast] Warning: bind failed on port {self.port}: {e}")

        self.socket.settimeout(0.1)
        print(f"[Broadcast] Socket ready on port {self.port}")

    def announce_game(self, host_name: str, game_port: int) -> bool:
        """Broadcast a GAME_ANNOUNCEMENT packet."""
        if not self.socket:
            print("[Broadcast] Socket not initialized")
            return False

        message = encode_message({
            "message_type": "GAME_ANNOUNCEMENT",
            "host_name": host_name,
            "game_port": str(game_port)
        })

        try:
            self.socket.sendto(message, ("<broadcast>", self.port))
            return True
        except Exception as e:
            print(f"[Broadcast] Error announcing game: {e}")
            return False

    def listen_for_games(self, timeout: float = 5.0) -> List[Tuple[str, str, int]]:
        """Listen for announcements and return available games."""
        if not self.socket:
            print("[Broadcast] Socket not initialized")
            return []

        print(f"[Broadcast] Listening for games for {timeout}s...")

        games = []
        elapsed = 0.0
        self.socket.settimeout(0.1)

        while elapsed < timeout:
            try:
                data, addr = self.socket.recvfrom(4096)
                msg = decode_message(data)

                if msg.get("message_type") == "GAME_ANNOUNCEMENT":
                    host_name = msg.get("host_name", "Unknown")
                    game_port = int(msg.get("game_port", 0))
                    ip = addr[0]

                    if not any(g[1] == ip and g[2] == game_port for g in games):
                        games.append((host_name, ip, game_port))
                        print(f"[Broadcast] Found game: {host_name} @ {ip}:{game_port}")

            except socket.timeout:
                pass
            except Exception as e:
                print(f"[Broadcast] Error receiving: {e}")

            elapsed += 0.1

        return games

    def close(self) -> None:
        if self.socket:
            self.socket.close()
            self.socket = None
            print("[Broadcast] Socket closed")