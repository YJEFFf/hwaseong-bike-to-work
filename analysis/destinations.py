"""목적지(학교·버스정류장·지하철역) 로드 -> EPSG:5186 좌표.

- `load_schools`: 전국초중등학교위치표준데이터 standard.json 페이지들에서 대상
  주소 토큰(기본 화성시)을 포함하고 OPER_STTUS == '운영'인 학교만 남긴다.
- `load_osm_pois`: OSM Overpass JSON의 node element에서 버스정류장/역만 남긴다.
  같은 노드가 정류장·역 태그를 모두 가지면 역(station)으로 한 번만 센다.
"""
import json

import pandas as pd
from pyproj import Transformer

_TO_5186 = Transformer.from_crs(4326, 5186, always_xy=True)


def load_schools(page_paths, address_token: str = "화성시") -> pd.DataFrame:
    names, kinds, xs, ys = [], [], [], []

    for path in page_paths:
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        for row in rows:
            addr = (row.get("RDNMADR") or "") + " " + (row.get("LNMADR") or "")
            if address_token not in addr:
                continue
            if row.get("OPER_STTUS") != "운영":
                continue
            lon = float(row["LONGITUDE"])
            lat = float(row["LATITUDE"])
            x, y = _TO_5186.transform(lon, lat)
            names.append(row.get("SCHOOL_NM"))
            kinds.append(row.get("SCHOOL_SE"))
            xs.append(x)
            ys.append(y)

    return pd.DataFrame({"name": names, "kind": kinds, "x": xs, "y": ys})


def load_osm_pois(json_path) -> pd.DataFrame:
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    names, kinds, xs, ys = [], [], [], []

    for el in data.get("elements", []):
        if el.get("type") != "node":
            continue
        tags = el.get("tags", {})

        is_station = tags.get("railway") == "station" or tags.get("public_transport") == "station"
        is_bus_stop = tags.get("highway") == "bus_stop"

        if is_station:
            kind = "station"
        elif is_bus_stop:
            kind = "bus_stop"
        else:
            continue

        lon, lat = el.get("lon"), el.get("lat")
        if lon is None or lat is None:
            continue
        x, y = _TO_5186.transform(lon, lat)
        names.append(tags.get("name", ""))
        kinds.append(kind)
        xs.append(x)
        ys.append(y)

    return pd.DataFrame({"name": names, "kind": kinds, "x": xs, "y": ys})
