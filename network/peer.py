import time
import threading
from enum import Enum
from typing import Optional, Tuple, Callable

from network.udp_transport import UDPTransport
from protocol.message import Message

class PeerRole(Enum):
    HOST = "HOST"
    JOINER = "JOINER"
    SPECTATOR = "SPECTATOR"

class Peer:
    def __init__(self, role: PeerRole, address: Tuple[str, int], verbose : bool = False):
        # Initialize peer with role, address, and verbosity
        self.role = role
        self.address = address
        self.verbose = verbose

        # Initialize UDP transport
        self.transport = UDPTransport(address)
        
        # Initialize connection state
        self.opponent_address = None
        self.seed = None
        self.connected = False
        
        # Initialize threading for listening to incoming messages        
        self.running = False
        self.listener_thread = None
        
        # Initialize message handlers
        self.message_handlers = {}
        
    def start(self):
        self.transport.start()
        self.running = True
        
        self.listener_thread = threading.Thread(target=self._receive_loop, daemon=True)
        self.listener_thread.start()
        print(f"[PEER] Started as {self.role.value} on port {self.port}")
        
    def stop(self):
        self.running = False
        if self.listener_thread:
            self.listener_thread.join(timeout=1.0)
        self.transport.close()
        
        print("[PEER] Stopped")
    
    def _receive_loop(self):
        while self.running:
            message = self.transport.receive()
            
            if message:
                data, addr = message
                try:
                    message = Message.deserialize(data)
                    self._handle_message(message, addr)
                except Exception as e:
                    print(f"[PEER] Error handling message: {e}")
            time.sleep(0.01)
    
    def _handle_message(self, message: Message, addr: Tuple[str, int]):
        if self.verbose:
            print(f"[RECV] {message.type} from {addr[0]}:{addr[1]}")
            print(f"       {message.fields}")
            
        handler = self.message_handlers.get(message.type)
        if handler:
            handler(message, addr)
        else:
            print(f"[PEER] No handler for message type: {message.type}")
    
    def register_handler(self, message_type: str, handler: Callable):
        self.message_handlers[message_type] = handler
    
    def send_message(self, message: Message, addr: Tuple[str, int]):
        if self.verbose:
            print(f"[SEND] {message.type} to {addr[0]}:{addr[1]}")
            print(f"       {message.fields}")
        
        data = message.serialize()
        self.transport.send(data, addr)