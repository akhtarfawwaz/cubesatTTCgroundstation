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
        "packet_id": packet_id,
        "timestamp": int(time.time()),
        "temperature": round(27.0 + random.uniform(-2, 2), 2),
        "pressure": round(101325 + random.uniform(-100, 100), 2),
        "altitude": round(114.0 + random.uniform(-1, 1), 2),
        "roll": round(math.sin(packet_id * 0.1) * 15, 2),
        "pitch": round(math.cos(packet_id * 0.1) * 10, 2),
        "yaw": round((packet_id * 5) % 360, 2),
        "latitude": round(-7.7956 + random.uniform(-0.0001, 0.0001), 6),
        "longitude": round(110.3695 + random.uniform(-0.0001, 0.0001), 6),
        "ack": 0
    }

    #Compute and attach CRC-16 
    data_bytes = json.dumps(packet, separators=(',', ':')).encode('utf-8')
    packet["crc"] = compute_crc16(data_bytes)

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