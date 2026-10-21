"""출발지(인구 가중 대표점)·목적지(학교·버스정류장·역) 적재.

사용법: python scripts/02b_origins_destinations.py --raw <raw_dir>

읽는 파일 (raw_dir 기준, 기본 config.RAW_DIR):
- population/pop_raw.bin            행정안전부 행정동별 주민등록인구 CSV (cp949)
- admin/hwaseong_hjd_20260701.geojson  읍면동 경계 (vuski/admdongkor, WGS84)
  controller 판정(2026-09-15): OSM Overpass 기반 osm_poi/admin.json은 member
  geometry가 없고 29개 읍면동 중 13개만 잡혀 실사용 불가 — 이 GeoJSON으로 대체.
- ghsl/*.tif                        GHS_POP_E2025 100m 래스터 (ESRI:54009, 여러
  타일이면 모두 읽어 합친다 — 겹치지 않는 타일은 윈도우가 비어 자동으로 무시됨)
- schools/page_*.json                전국초중등학교위치표준데이터
- osm_poi/stops.json                 버스정류장/역 OSM POI

산출:
- outputs/origins_5186.csv       columns: x, y, pop, dong
- outputs/destinations_5186.csv  columns: name, kind, x, y

필요한 raw 파일이 하나라도 없거나 읽을 수 없으면 그 목록을 stderr에 출력하고
종료 코드 1로 끝난다 (아무 산출물도 쓰지 않음).

주의: 인구 총량과 경계 폴리곤이 이름으로 매칭되지 않는 읍면동은 dasymetric_points가
조용히 인구 0으로 취급해 출발지에서 빠진다 (화성시는 unmatched 0건으로 검증됨). 다른
지자체로 재사용해 `pop_unmatched_dongs`(numbers.csv)가 0이 아니면, 위 stdout에 찍히는
"unmatched (population side/polygon side)" 목록을 반드시 사람이 확인할 것.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from analysis import config
from analysis.admin import polygons_from_geojson
from analysis.destinations import load_osm_pois, load_schools
from analysis.numbers import record
from analysis.population import dasymetric_points, load_dong_population


def normalize_name(name: str) -> str:
    return name.strip().replace(" ", "")


def _check_inputs(raw_dir: Path):
    """필요한 raw 파일을 찾는다. (문제 목록, 경로/글롭 결과 딕셔너리)를 반환."""
    problems = []

    pop_path = raw_dir / "population" / "pop_raw.bin"
    if not pop_path.exists():
        problems.append(f"missing: {pop_path}")

    admin_path = raw_dir / "admin" / "hwaseong_hjd_20260701.geojson"
    if not admin_path.exists():
        problems.append(f"missing: {admin_path}")

    ghsl_dir = raw_dir / "ghsl"
    ghsl_tifs = sorted(ghsl_dir.glob("*.tif")) if ghsl_dir.exists() else []
    if not ghsl_tifs:
        problems.append(f"missing: {ghsl_dir}/*.tif (no tiles found)")

    schools_dir = raw_dir / "schools"
    school_pages = sorted(schools_dir.glob("page_*.json")) if schools_dir.exists() else []
    if not school_pages:
        problems.append(f"missing: {schools_dir}/page_*.json (no pages found)")

    stops_path = raw_dir / "osm_poi" / "stops.json"
    if not stops_path.exists():
        problems.append(f"missing: {stops_path}")
    else:
        try:
            with open(stops_path, encoding="utf-8") as f:
                json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            problems.append(f"invalid JSON (likely a failed/partial fetch): {stops_path} ({e})")

    return problems, {
        "pop_path": pop_path,
        "admin_path": admin_path,
        "ghsl_tifs": ghsl_tifs,
        "school_pages": school_pages,
        "stops_path": stops_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default=str(config.RAW_DIR), help="raw 데이터 디렉터리")
    args = parser.parse_args()
    raw_dir = Path(args.raw)

    problems, paths = _check_inputs(raw_dir)
    if problems:
        print("Missing/invalid raw inputs -- stopping before writing outputs:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        sys.exit(1)

    # ---- 출발지: 인구 가중 대표점 ----
    dong_pop = load_dong_population(paths["pop_path"], sgg_prefix="화성시")
    record("pop_dong_rows", len(dong_pop), "화성시 읍면동 인구 행 수 (population/pop_raw.bin)")
    record("pop_total_hwaseong", int(dong_pop["total"].sum()), "화성시 주민등록인구 합계")

    dong_polys_raw = polygons_from_geojson(paths["admin_path"], name_field="adm_nm")

    pop_display = {}
    dong_totals = {}
    for _, row in dong_pop.iterrows():
        norm = normalize_name(row["dong"])
        pop_display[norm] = row["dong"]
        dong_totals[norm] = int(row["total"])

    poly_by_norm = {normalize_name(name): geom for name, geom in dong_polys_raw.items()}

    matched = sorted(set(pop_display) & set(poly_by_norm))
    unmatched_pop = sorted(set(pop_display) - set(poly_by_norm))
    unmatched_poly = sorted(set(poly_by_norm) - set(pop_display))

    print(f"unmatched (population side, no polygon) [{len(unmatched_pop)}]: "
          f"{[pop_display[n] for n in unmatched_pop]}")
    print(f"unmatched (polygon side, no population row) [{len(unmatched_poly)}]: {unmatched_poly}")

    record(
        "pop_unmatched_dongs",
        len(unmatched_pop) + len(unmatched_poly),
        "읍면동 이름 매칭 실패 수 (인구 쪽 + 경계 쪽 합, 정규화 후 정확 일치 기준)",
    )

    if unmatched_pop or unmatched_poly:
        raise ValueError("population/boundary names mismatch: no silent omission allowed")
    dong_polys = {n: poly_by_norm[n] for n in matched}
    dong_totals_matched = {n: dong_totals[n] for n in matched}

    origins = dasymetric_points(paths["ghsl_tifs"], dong_polys, dong_totals_matched)
    record("origins_cells", len(origins), "출발지(인구 가중 대표점) 셀 수 (pop > 0)")

    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    origins.to_csv(config.OUTPUTS / "origins_5186.csv", index=False, encoding="utf-8-sig")

    # ---- 목적지: 학교 / 버스정류장 / 역 ----
    schools = load_schools(paths["school_pages"], address_token="화성시")
    record("dest_schools", len(schools), "화성시 운영 초중고 수 (표준데이터)")

    pois = load_osm_pois(paths["stops_path"])
    n_bus = int((pois["kind"] == "bus_stop").sum())
    n_station = int((pois["kind"] == "station").sum())
    record("dest_bus_stops", n_bus, "화성시 버스정류장 OSM POI 수")
    record("dest_stations", n_station, "화성시 지하철/철도역 OSM POI 수")

    destinations = pd.concat([schools, pois], ignore_index=True)
    destinations.to_csv(config.OUTPUTS / "destinations_5186.csv", index=False, encoding="utf-8-sig")

    summary = {
        "pop_dong_rows": len(dong_pop),
        "pop_total_hwaseong": int(dong_pop["total"].sum()),
        "unmatched_population_side": [pop_display[n] for n in unmatched_pop],
        "unmatched_polygon_side": unmatched_poly,
        "origins_cells": len(origins),
        "dest_schools": len(schools),
        "dest_bus_stops": n_bus,
        "dest_stations": n_station,
        "origins_csv": str(config.OUTPUTS / "origins_5186.csv"),
        "destinations_csv": str(config.OUTPUTS / "destinations_5186.csv"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
