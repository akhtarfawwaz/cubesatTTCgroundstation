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
            "p_id", "timestamp",           
            "mode",                         
            "temperature", "pressure", "altitude",
            "accel_x", "accel_y", "accel_z",  
            "gyro_x", "gyro_y", "gyro_z",     
            "roll", "pitch", "yaw",
            "latitude", "longitude",
            "gps_alt",                      
            "sats", "volt", "err",          
            "ack", "crc16"                  
        ]
        
        for field in required_fields:
            if field not in packet:
                print(f"[PARSER] Missing field: {field}")
                return None
            
        
        #Validate CRC-16
        received_crc = int(packet["crc16"], 16)
        packet_without_crc = {k: v for k, v in packet.items() if k != "crc16"}
        data_bytes = json.dumps(packet_without_crc, separators=(',', ':')).encode('utf-8')

        if not validate_crc16(data_bytes, received_crc):
            print(f"[PARSER] CRC validation failed for p_id: {packet.get('p_id')}")
            return None

        print(f"[PARSER] Valid packet received: p_id={packet['p_id']}")
        return packet
    
    except json.JSONDecodeError as e:
        print(f"[PARSER] JSON decode error: {e}")
        return None