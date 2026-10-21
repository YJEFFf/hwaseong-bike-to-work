"""OSM 자전거 시설 통계 (필터 적용).

사용법: python scripts/01b_osm_stats.py

data/raw/의 OSM Overpass JSON(config.OSM_BIKE)을 읽어 is_bike_facility()로
실제 자전거 시설 way만 남긴 뒤, 총 way 수/총 연장(km)과 좌표 공유 기반
연결요소(union-find, 좌표 소수점 7자리 반올림) 및 최대 연결요소 연장을
계산한다. outputs/numbers.csv에 핵심 수치를 기록한다.

기대값: ways 1700, total 264.7 km, components 60.
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import config
from analysis.numbers import record
from analysis.osm_filter import is_bike_facility

EARTH_RADIUS_M = 6371000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def way_length_m(geometry: list) -> float:
    return sum(
        haversine_m(geometry[i]["lat"], geometry[i]["lon"], geometry[i + 1]["lat"], geometry[i + 1]["lon"])
        for i in range(len(geometry) - 1)
    )


def node_key(pt: dict) -> tuple:
    return (round(pt["lat"], 7), round(pt["lon"], 7))


class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def load_bike_facility_ways(path) -> list:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [
        el for el in data["elements"]
        if el.get("type") == "way" and is_bike_facility(el.get("tags", {}))
    ]


def compute_stats(ways: list) -> dict:
    total_m = 0.0
    uf = UnionFind()
    way_lengths_m = []
    way_endpoints = []

    for way in ways:
        geometry = way.get("geometry") or []
        length_m = way_length_m(geometry)
        total_m += length_m
        way_lengths_m.append(length_m)
        if geometry:
            for pt in geometry:
                uf.find(node_key(pt))
            for i in range(len(geometry) - 1):
                uf.union(node_key(geometry[i]), node_key(geometry[i + 1]))
            way_endpoints.append(node_key(geometry[0]))
        else:
            way_endpoints.append(None)

    component_km = {}
    for length_m, endpoint in zip(way_lengths_m, way_endpoints):
        if endpoint is None:
            continue
        root = uf.find(endpoint)
        component_km[root] = component_km.get(root, 0.0) + length_m / 1000

    n_components = len(component_km)
    lcc_km = max(component_km.values()) if component_km else 0.0

    return {
        "ways": len(ways),
        "total_km": round(total_m / 1000, 1),
        "components": n_components,
        "lcc_km": round(lcc_km, 1),
    }


def main() -> None:
    ways = load_bike_facility_ways(config.OSM_BIKE)
    stats = compute_stats(ways)

    record("osm_facility_ways", stats["ways"], "is_bike_facility 필터 통과 way 수")
    record("osm_facility_km", stats["total_km"], "필터 통과 way 총 연장(km, haversine)")
    record("osm_components", stats["components"], "좌표 공유 기반 연결요소 수")
    record("osm_lcc_km", stats["lcc_km"], "최대 연결요소(LCC) 연장(km)")

    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
