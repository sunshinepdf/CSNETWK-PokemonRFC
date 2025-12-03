import json
from typing import Dict, Any, Optional

class Message:
    """Represents a protocol message"""
    
    def __init__(self, message_type: str, **fields):
        self.type = message_type
        self.fields = fields
    
    def serialize(self) -> bytes:
        """Convert message to wire format (key: value pairs)"""
        lines = [f"message_type: {self.type}"]
        
        for key, value in self.fields.items():
            # Handle nested objects (like pokemon data, stat_boosts)
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            lines.append(f"{key}: {value}")
        
        return "\n".join(lines).encode('utf-8')
    
    @staticmethod
    def deserialize(data: bytes) -> 'Message':
        """Parse message from wire format"""
        try:
            text = data.decode('utf-8').strip()
            lines = text.split('\n')
            
            fields = {}
            message_type = None
            
            for line in lines:
                if ': ' not in line:
                    continue
                    
                key, value = line.split(': ', 1)
                
                if key == 'message_type':
                    message_type = value
                else:
                    # Try to parse JSON if it looks like JSON
                    if value.startswith('{') or value.startswith('['):
                        try:
                            value = json.loads(value)
                        except:
                            pass  # Keep as string if JSON parse fails
                    fields[key] = value
            
            return Message(message_type, **fields)
        
        except Exception as e:
            raise ValueError(f"Failed to deserialize message: {e}")
    
    def __repr__(self):
        return f"Message({self.type}, {self.fields})"