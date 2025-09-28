KM_TO_MI = 0.621371

def km_to_mi(v):
    try:
        return float(v) * KM_TO_MI
    except Exception:
        return None
