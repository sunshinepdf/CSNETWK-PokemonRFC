import time
import threading
from enum import Enum
from typing import Optional, Tuple, Callable

from network.udp_transport import UDPTransport

class PeerRole(Enum):
    HOST = "HOST"
    JOINER = "JOINER"
    SPECTATOR = "SPECTATOR"

class Peer:
    def __init__(self, role: PeerRole, address: Tuple[str, int], verbose : bool = False, on_disconnect: Optional[Callable[['Peer'], None]] = None):
        self.role = role