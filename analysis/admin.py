"""행정경계 -> 폴리곤(EPSG:5186).

두 가지 소스를 지원한다:

- `polygons_from_overpass`: OSM Overpass `rel["boundary"="administrative"]
  ["admin_level"~"^(7|8)$"]`를 `out tags geom`으로 받은 JSON의 `elements`.
  각 relation의 member way geometry(위경도)로 LineString을 만들고
  `shapely.ops.polygonize`로 outer 링을 합쳐 폴리곤을 만든다. inner member가
  있으면 같은 방식으로 만든 뒤 outer에서 빼서 구멍을 낸다. 이름은 relation의
  `name` 태그를 그대로 키로 쓴다.
  (controller 판정: 실제 OSM admin.json은 member geometry가 없고 29개 중
  13개만 있어 실사용 데이터에는 쓰지 않는다. vuski/admdongkor GeoJSON으로
  대체 — 아래 `polygons_from_geojson`. 이 함수는 테스트된 상태로 남겨둔다.)
- `polygons_from_geojson`: vuski/admdongkor류 WGS84 GeoJSON FeatureCollection.
  각 feature의 `name_field`(기본 `adm_nm`, 예: "경기도 화성시효행구 봉담읍")의
  마지막 공백 토큰(읍면동명, 예: "봉담읍")을 키로 쓴다.
"""
import json

from pyproj import Transformer
from shapely.geometry import LineString, shape
from shapely.ops import polygonize, transform as shp_transform, unary_union

_TO_5186 = Transformer.from_crs(4326, 5186, always_xy=True)


def _project(geom):
    return shp_transform(lambda x, y: _TO_5186.transform(x, y), geom)


def _member_lines(members, role_wanted):
    lines = []
    for m in members:
        if m.get("type") != "way":
            continue
        role = m.get("role", "outer")
        if role == "inner":
            if role_wanted != "inner":
                continue
        else:
            if role_wanted != "outer":
                continue
        geom = m.get("geometry")
        if not geom or len(geom) < 2:
            continue
        coords = [(pt["lon"], pt["lat"]) for pt in geom]
        lines.append(LineString(coords))
    return lines


def polygons_from_overpass(elements: list[dict], admin_level: str = "8") -> dict:
    result = {}
    for el in elements:
        if el.get("type") != "relation":
            continue
        tags = el.get("tags", {})
        if tags.get("boundary") != "administrative":
            continue
        if str(tags.get("admin_level")) != str(admin_level):
            continue
        name = tags.get("name")
        members = el.get("members", [])

        outer_lines = _member_lines(members, "outer")
        if not outer_lines:
            continue
        outer_polys = list(polygonize(unary_union(outer_lines)))
        if not outer_polys:
            continue
        geom = unary_union(outer_polys)

        inner_lines = _member_lines(members, "inner")
        if inner_lines:
            inner_polys = list(polygonize(unary_union(inner_lines)))
            if inner_polys:
                geom = geom.difference(unary_union(inner_polys))

        if geom.is_empty:
            continue

        result[name] = _project(geom)
    return result


def polygons_from_geojson(path, name_field: str = "adm_nm") -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    result = {}
    for feat in data.get("features", []):
        props = feat.get("properties", {}) or {}
        raw_name = props.get(name_field)
        if not raw_name or not str(raw_name).strip():
            continue
        dong = str(raw_name).split()[-1]
        geom = shape(feat["geometry"])
        if geom.is_empty:
            continue
        result[dong] = _project(geom)
    return result
