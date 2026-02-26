
# --- HELPER FUNCTIONS ---

# hW = 100W, kW = 1000W => 1kW = 10hW
def kw_to_hw(kw):
    return int(kw * 10)

def hw_to_kw(hw):
    return float(hw / 10.0)

# hWh = 100Wh, kWh = 1000Wh => 1kWh = 10hWh
def kwh_to_hwh(kwh):
    return int(kwh * 10)

def hwh_to_kwh(hwh):
    return float(hwh / 10.0)


# --- 16-bit Signed Integer Handling for Modbus ---

def int_to_uint16(value):
    """
    Convert a signed integer to an unsigned 16-bit value for Modbus register.
    Uses two's complement representation for negative values.
    
    Args:
        value: Signed integer (range: -32768 to 32767)
    
    Returns:
        Unsigned 16-bit integer (range: 0 to 65535)
    """
    value = int(value)
    if value < 0:
        return value + 65536  # Two's complement for negative
    return value & 0xFFFF


def uint16_to_int(value):
    """
    Convert an unsigned 16-bit Modbus register value to a signed integer.
    Interprets values >= 32768 as negative (two's complement).
    
    Args:
        value: Unsigned 16-bit integer from Modbus register (range: 0 to 65535)
    
    Returns:
        Signed integer (range: -32768 to 32767)
    """
    if value >= 32768:  # If highest bit is set, it's negative
        return value - 65536
    return value