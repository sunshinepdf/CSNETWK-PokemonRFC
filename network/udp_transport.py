import socket
import threading
import queue
from typing import Optional, Tuple

class UDPTransport:
    def __init__(self, port: int, host: str):
        self.host = host
        self.port = port
        self.socket = None

    def open(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        self.socket.bind((self.host, self.port))
        
        self.socket.settimeout(0.1)
        
        self.running = True
        print(f"[UDP] Socket bound to {self.host}:{self.port}")
