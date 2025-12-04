"""
protocol/broadcast.py

Implements broadcast mode for peer discovery on local network.

This allows hosts to announce their game availability and joiners
to discover available games without knowing the exact IP address.
"""

import socket
from typing import Optional, Tuple, List
from .message import encode_message, decode_message


class BroadcastDiscovery:
    """Handles UDP broadcast for game discovery on local network."""
    
    def __init__(self, port: int = 5555):
        """Initialize with broadcast port."""
        self.port = port
        self.socket: Optional[socket.socket] = None
    
    def open(self) -> None:
        """Initialize broadcast socket."""
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.settimeout(0.1)
        print(f"[Broadcast] Socket initialized on port {self.port}")
    
    def announce_game(self, host_name: str, game_port: int) -> bool:
        """
        Broadcast game availability to local network.
        
        Args:
            host_name: Name of the host
            game_port: Port where the actual game will be hosted
            
        Returns:
            True if sent successfully
        """
        if not self.socket:
            print("[Broadcast] Socket not initialized")
            return False
        
        message = encode_message({
            "message_type": "GAME_ANNOUNCEMENT",
            "host_name": host_name,
            "game_port": str(game_port)
        })
        
        try:
            self.socket.sendto(message, ('<broadcast>', self.port))
            return True
        except Exception as e:
            print(f"[Broadcast] Error announcing game: {e}")
            return False
    
    def listen_for_games(self, timeout: float = 5.0) -> List[Tuple[str, str, int]]:
        """
        Listen for game announcements on the network.
        
        Args:
            timeout: How long to listen in seconds
            
        Returns:
            List of (host_name, ip_address, game_port) tuples
        """
        if not self.socket:
            print("[Broadcast] Socket not initialized")
            return []
        
        try:
            self.socket.bind(('', self.port))
        except OSError:
            # Already bound
            pass
        
        games = []
        self.socket.settimeout(timeout)
        
        print(f"[Broadcast] Listening for games for {timeout}s...")
        
        try:
            while True:
                try:
                    data, addr = self.socket.recvfrom(4096)
                    msg = decode_message(data)
                    
                    if msg.get("message_type") == "GAME_ANNOUNCEMENT":
                        host_name = msg.get("host_name", "Unknown")
                        game_port = int(msg.get("game_port", 0))
                        ip = addr[0]
                        
                        if not any(g[1] == ip and g[2] == game_port for g in games):
                            games.append((host_name, ip, game_port))
                            print(f"[Broadcast] Found game: {host_name} at {ip}:{game_port}")
                
                except socket.timeout:
                    break
                except Exception as e:
                    print(f"[Broadcast] Error receiving: {e}")
                    break
        
        finally:
            self.socket.settimeout(0.1)
        
        return games
    
    def close(self) -> None:
        """Close broadcast socket."""
        if self.socket:
            self.socket.close()
            print("[Broadcast] Socket closed")