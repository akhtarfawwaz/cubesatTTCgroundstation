#CRC-16 Calculation Ulitity 

def compute_crc16(data: bytes) -> int:
    """
    Compute CRC-16 checksum on raw bytes.
    Uses CRC-16/CCITT-FALSE polunomial (0xFFFF).
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF  
    return crc


def validate_crc16(data: bytes, received_crc: int) -> bool:
    """
    Validate CRC-16 by comparing computed vs received.
    Returns True if valid, False if corrupted.
    """
    computed = compute_crc16(data)
    return computed == received_crc