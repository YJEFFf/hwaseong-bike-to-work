"""저위계 도로 허용망(low-stress network) 로더.

Task 12(자전거 시설 전용망)와 별개로, 자전거 시설 + 자전거로 이용 가능한
OSM 소로(analysis.osm_filter.is_low_stress)를 EPSG:5186 LineString 목록으로
만든다.
"""
import json

from pyproj import Transformer
from shapely.geometry import LineString

from analysis.osm_filter import is_low_stress

_TO_5186 = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True)


def load_low_stress_lines(json_path) -> list:
    """Overpass `out geom` JSON(roads.json)에서 저위계 허용망 LineString을 만든다.

    각 way의 geometry는 [{lat, lon}, ...] 목록이다. EPSG:4326 -> EPSG:5186
    변환은 pyproj Transformer(always_xy=True)로 (lon, lat) -> (x, y) 순서로
    수행한다. 좌표가 2개 미만인 way는 LineString을 만들 수 없어 건너뛴다.
    """
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    lines = []
    for el in data.get("elements", []):
        if el.get("type") != "way":
            continue
        tags = el.get("tags", {}) or {}
        if not is_low_stress(tags):
            continue
        geometry = el.get("geometry") or []
        if len(geometry) < 2:
            continue
        coords = [_TO_5186.transform(pt["lon"], pt["lat"]) for pt in geometry]
        lines.append(LineString(coords))

    return lines
