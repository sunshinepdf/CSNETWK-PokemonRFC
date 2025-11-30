import socket
from typing import Optional, Tuple

class UDPTransport:
    def __init__(self, port: int, host: str):
        self.port = port
        self.host = host
        self.socket = None
        self.running = False

    def open(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        self.socket.bind((self.host, self.port))
        
        self.socket.settimeout(0.1)
        
        self.running = True
        print(f"[UDP] Socket bound to {self.host}:{self.port}")

    def send(self, data: bytes, addr: Tuple[str, int]):
        try:
            self.socket.sendto(data, addr)
            return True
        except Exception as e:
            print(f"[UDP] Send error: {e}")
            return False
    
    def receive(self) -> Optional[Tuple[bytes, Tuple[str, int]]]:
        try:
            data, addr = self.socket.recvfrom(65535)
            return data, addr
        except socket.timeout:
            return None
        except Exception as e:
            print(f"[UDP] Receive error: {e}")
            return None
        
    def close(self):
        self.running = False
        if self.socket:
            self.socket.close()
            print(f"[UDP] Socket on {self.host}:{self.port} closed")
