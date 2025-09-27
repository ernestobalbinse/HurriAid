# Message Contracts

## Watcher
**Input:** `{ "offline": boolean }`
**Output:**
```json
{
    "advisory": { "issued_at": "ISO8601", "center": {"lat": number, "lon": number}, "radius_km": number, "category": "TS|CAT1|CAT2|CAT3|CAT4|CAT5" },
    "zip_centroids": { "ZIP": {"lat": number, "lon": number}, ... },
    "shelters": [ { "name": string, "lat": number, "lon": number, "is_open": boolean }, ... ]
}