#Distance Calculations
from math import radians, sin, cos, asin, sqrt, degrees, atan2


def haversine_km(lat1, lon1, lat2, lon2):
	R = 6371.0 # Earth radius in km
	dlat = radians(lat2 - lat1)
	dlon = radians(lon2 - lon1)
	a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
	c = 2 * asin(sqrt(a))
	return R * c

KM_PER_DEG_LAT = 111.32

def circle_polygon(lat, lon, radius_km: float, num_points: int = 72):
	"""Return a list of [lon, lat] points approximating a circle on WGS84 for small radii."""
	pts = []
	lat_rad = radians(lat)
	deg_lon_per_km = 1.0 / (KM_PER_DEG_LAT * cos(lat_rad) if cos(lat_rad) != 0 else 1e-9)
	deg_lat_per_km = 1.0 / KM_PER_DEG_LAT
	for i in range(num_points):
		theta = 2.0 * 3.141592653589793 * (i / num_points)
		dlat = (radius_km * sin(theta)) * deg_lat_per_km
		dlon = (radius_km * cos(theta)) * deg_lon_per_km
		pts.append([lon + dlon, lat + dlat])
	# close polygon
	pts.append(pts[0])
	return pts