import hashlib
import json

from shapely.geometry import shape


def _round(x):
    if isinstance(x, list):
        return [_round(v) for v in x]
    if isinstance(x, float):
        return round(x, 3)  # EPSG:5186 미터 → mm
    return x


def canonical_hash(fc: dict) -> str:
    feats = sorted(fc["features"], key=lambda f: str(f.get("id")))
    norm = [{"id": f.get("id"), "properties": f.get("properties"),
             "geometry": {"type": f["geometry"]["type"], "coordinates": _round(f["geometry"]["coordinates"])}}
            for f in feats]
    blob = json.dumps(norm, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def load_fc(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def verify_official(lines_fc: dict, polys_fc: dict) -> dict:
    line_km = sum(shape(f["geometry"]).length for f in lines_fc["features"]) / 1000
    return {"lines": len(lines_fc["features"]), "polys": len(polys_fc["features"]),
            "line_km": round(line_km, 3),
            "lines_hash": canonical_hash(lines_fc), "polys_hash": canonical_hash(polys_fc)}
