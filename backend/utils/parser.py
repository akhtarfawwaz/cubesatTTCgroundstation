#Telemetry Packet Parser

import json
from utils.crc import validate_crc16


def parse_telemetry(raw: str) -> dict | None:
    """
    Parse and validate incoming telemetry JSON string from ESP32.
    Returns parsed dict if valid, None if corrupted or invalid.
    """
    try:
        packet = json.loads(raw)

        #Check for required fields
        required_fields = [
            "packet_id", "timestamp",
            "temperature", "pressure", "altitude",
            "roll", "pitch", "yaw",
            "latitude", "longitude",
            "ack", "crc"
        ]
        for field in required_fields:
            if field not in packet:
                print(f"[PARSER] Missing field: {field}")
                return None
            
        
        #Validate CRC-16
        received_crc = packet["crc"]
        packet_without_crc = {k: v for k, v in packet.items() if k != "crc"}
        data_bytes = json.dumps(packet_without_crc, separators=(',', ':')).encode('utf-8')

        if not validate_crc16(data_bytes, received_crc):
            print(f"[PARSER] CRC validation failed for packet_id: {packet.get('packet_id')}")
            return None
        
        print(f"[PARSER] Valid packet received: packet_id={packet['packet_id']}")
        return packet
    
    except json.JSONDecodeError as e:
        print(f"[PARSER] JSON decode error: {e}")
        return None