import json
import time
import math
import random
from utils.crc import compute_crc16


def generate_mock_packet(packet_id: int) -> str:
    """
    Generate a realistic fake telemetry packet as if coming from ESP32.
    """

    #Simulate sensor values
    packet = {
    "p_id": packet_id,                                        
    "timestamp": int(time.time()),
    "mode": 2,                                                   
    "temperature": round(27.0 + random.uniform(-2, 2), 2),
    "pressure": round(101325 + random.uniform(-100, 100), 2),
    "altitude": round(114.0 + random.uniform(-1, 1), 2),
    "accel_x": round(random.uniform(-0.5, 0.5), 2),           
    "accel_y": round(random.uniform(-0.5, 0.5), 2),            
    "accel_z": round(9.81 + random.uniform(-0.1, 0.1), 2),    
    "gyro_x": round(random.uniform(-0.05, 0.05), 4),           
    "gyro_y": round(random.uniform(-0.05, 0.05), 4),           
    "gyro_z": round(random.uniform(-0.05, 0.05), 4),           
    "roll": round(math.sin(packet_id * 0.1) * 15, 2),          
    "pitch": round(math.cos(packet_id * 0.1) * 10, 2),         
    "yaw": round((packet_id * 5) % 360, 2),                    
    "latitude": round(-7.7956 + random.uniform(-0.0001, 0.0001), 6),
    "longitude": round(110.3695 + random.uniform(-0.0001, 0.0001), 6),
    "gps_alt": round(114.0 + random.uniform(-1, 1), 2),        
    "sats": random.randint(4, 8),                               
    "volt": round(random.uniform(3.6, 3.8), 2),            
    "err": 0,                                                   
    "ack": 0
}

    #Compute and attach CRC-16 
    data_bytes = json.dumps(packet, separators=(',', ':')).encode('utf-8')
    packet["crc16"] = format(compute_crc16(data_bytes), '04X')

    return json.dumps(packet, separators=(',', ':'))

def mock_serial_stream(interval: float = 2.0):
    """
    Generator that yields mock telemetry packets every `interval` seconds.
    """
    packet_id = 1
    while True:
        yield generate_mock_packet(packet_id)
        packet_id += 1
        time.sleep(interval)