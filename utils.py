
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