"""
protocol/broadcast.py

UDP broadcast-based game discovery for PokeProtocol.
"""

import socket
import time
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
        self.listen_only = False  

    def open(self, listen_only: bool = False) -> None:
        """Open and configure the broadcast socket.
        
        Args:
            listen_only: If True, bind for receiving (JOINER discovery mode).
                        If False, only set up for sending (HOST mode).
        """
        self.listen_only = listen_only
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

        # Only bind if we're listening for discoveries (JOINER mode)
        if listen_only:
            try:
                self.socket.bind(("", self.port))
                print(f"message_type: BROADCAST_INIT\nmode: listen\nport: {self.port}")
            except OSError as e:
                print(f"message_type: BROADCAST_ERROR\nerror: bind failed on port {self.port}: {e}")
        else:
            print(f"message_type: BROADCAST_INIT\nmode: send_only\nport: {self.port}")

        self.socket.settimeout(0.1)

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
        
        # Only listen if opened in listen_only mode
        if not self.listen_only:
            print("[Broadcast] Socket not in listen mode. Skipping listen_for_games().")
            return []

        print(f"[Broadcast] Listening for games for {timeout}s...")

        games = []
        start_time = time.time()
        self.socket.settimeout(0.1)

        while time.time() - start_time < timeout:
            try:
                data, addr = self.socket.recvfrom(4096)
                msg = decode_message(data)

                if msg.get("message_type") == "GAME_ANNOUNCEMENT":
                    host_name = msg.get("host_name", "Unknown")
                    game_port = int(msg.get("game_port", 0))
                    ip = addr[0]

                    # Only add if we haven't seen this exact game before
                    if not any(g[1] == ip and g[2] == game_port for g in games):
                        games.append((host_name, ip, game_port))
                        print(f"[Broadcast] Found game: {host_name} @ {ip}:{game_port}")

            except socket.timeout:
                pass
            except Exception as e:
                print(f"[Broadcast] Error receiving: {e}")

        return games

    def close(self) -> None:
        if self.socket:
            self.socket.close()
            self.socket = None
            print("[Broadcast] Socket closed")