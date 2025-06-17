from django.conf import settings
from django.contrib.gis.geos import Point
from httpx import AsyncClient, Timeout

ip_api_client = AsyncClient(timeout=Timeout(1), base_url="http://ip-api.com/json/")


class GeolocationError(Exception):
    pass


async def get_ip_location(ip: str) -> Point:
    response = await ip_api_client.get(f"{ip}")
    try:
        response.raise_for_status()
    except Exception as e:
        raise GeolocationError(f"Failed to get location for IP {ip}: {e}") from e

    try:
        data = response.json()
    except Exception as e:
        raise GeolocationError(f"Failed to parse location data for IP {ip}: {e}") from e

    if data["status"] != "success":
        raise GeolocationError(f"Failed to get location for IP {ip}: {data['message']}")

    if not (lat := data.get("lat")) or not (lon := data.get("lon")):
        raise GeolocationError(f"Missing latitude or longitude for IP {ip}")

    return Point(float(lon), float(lat), srid=settings.GEODJANGO_SRID)
