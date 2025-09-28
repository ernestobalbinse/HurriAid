KM_TO_MI = 0.621371

def km_to_mi(v):                    # Convert km to mi for easier math
    try:
        return float(v) * KM_TO_MI
    except Exception:
        return None
