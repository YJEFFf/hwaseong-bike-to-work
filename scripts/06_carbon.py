"""Task 15b: 태양광 차양막 에너지 장부 + 교통 장부 (설명용) (plan Task 15 Steps 6-8,
controller-expanded).

정책 챕터 — 태양광 차양막(그늘+비가림+발전)을 두 생활권의 우선순위 링크
(S1_priority)와 폭이 넓은 기존 자전거도로 폴리곤(S2_wide_existing) 위에
제안한다. 같은 용량을 "공장 지붕에 설치했다면"과 나란히 보여준다.

**교통 장부와 에너지 장부는 절대 합산하지 않는다** — 단위와 질문 자체가
다르다 (analysis/ledgers.py, analysis/carbon.py 모듈 docstring 참고).
이를 코드로도 강제한다: 두 표를 별도 DataFrame으로 만들고,
`analysis.ledgers.assert_no_sum_columns`로 "total"/"합계" 열이 없는지,
그리고 각 표의 `ledger` 열이 하나의 값만 갖는지 확인한다.

siting sets (생활권마다):
- S1_priority: outputs/private/priority_links.csv 중 inclusion_reach>=0.6인
  링크. length_m = gap_m 합, 캐노피 폭은 3.0m 가정(assumption, 출력에 표시).
- S2_wide_existing: 공식 자전거도로면(화성_자전거도로면_5186.geojson) 중
  생활권과 교차하고 추정폭(strip_width = 2*area/perimeter) >= 3.0m인
  조각들 -- 덮는 면적 = 생활권으로 클립한 면적.
- 가로수 제거 가능 여부는 확인 불가(수목 데이터 없음) -> 모든 행에
  tree_check = "미확인(현장 확인 필요)".
- 버스정류장 대기공간은 포함하지 않는다(정류장당 출처 있는 면적 없음).

사용법:
    python scripts/06_carbon.py --repo-root <root> --regions <dong1>,<dong2>
"""
import argparse
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from shapely.geometry import shape

from analysis import config
from analysis.carbon import required_avoided_vkm
from analysis.io_canon import load_fc
from analysis.ledgers import (
    STRUCTURE_FACTORY_ROOF,
    assert_no_sum_columns,
    energy_rows,
    get_scenario_value,
    load_params,
    s2_wide_existing_area,
)
from analysis.numbers import record

SCENARIOS = ["low", "mid", "high"]
S1_WIDTH_M = 3.0
S2_MIN_WIDTH_M = 3.0
BENCHMARK_MWH_PER_KM_YR = 478.0
PRIORITY_INCLUSION_THRESHOLD = 0.6
TRANSPORT_EXTRA_EMISSIONS_T = [1, 5, 10, 50]

OFFICIAL_POLYS_REL = "data/raw/화성_자전거도로면_5186.geojson"
PRIORITY_LINKS_REL = "outputs/private/priority_links.csv"
PARAMS_REL = "params/carbon_params.csv"

TREE_CHECK_NOTE = "미확인(현장 확인 필요)"
BUSSTOP_NOTE = "버스정류장 대기공간 미포함(정류장당 출처 있는 면적 없음)"


def _load_region_polygons_fn():
    """scripts/05_priority.py의 region_polygons를 그대로 재사용한다 (Task
    13b와 동일한 생활권 정의 -- controller ruling 1). 파일명이 숫자로
    시작해 일반 `import` 문으로는 불러올 수 없어 importlib로 파일 경로를
    직접 로드한다. 로직을 복제하지 않기 위한 선택."""
    path = Path(__file__).resolve().parent / "05_priority.py"
    spec = importlib.util.spec_from_file_location("_priority_05_for_ledgers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.region_polygons


def load_official_polygons(repo_root: Path):
    """공식 자전거도로면 geojson(EPSG:5186, 이미 투영됨) -> 단일 Polygon
    리스트. MultiPolygon feature는 조각으로 풀어서 넣는다 -- strip_width
    근사는 조각 단위로만 의미가 있다 (analysis/figures.py와 동일 관례)."""
    fc = load_fc(repo_root / OFFICIAL_POLYS_REL)
    polys = []
    for feat in fc["features"]:
        geom = shape(feat["geometry"])
        geoms = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        polys.extend(geoms)
    return polys


def s1_priority_area(priority_df: pd.DataFrame, region: int):
    """S1_priority: 이 region의 inclusion_reach>=0.6 링크 gap_m 합 ->
    length_m, area_m2(폭 3.0m 가정). n_links도 반환(보고용)."""
    rows = priority_df[
        (priority_df["region"] == region)
        & (priority_df["inclusion_reach"] >= PRIORITY_INCLUSION_THRESHOLD)
    ]
    length_m = float(rows["gap_m"].sum())
    area_m2 = length_m * S1_WIDTH_M
    return length_m, area_m2, len(rows)


def _param_status_note(params, names):
    bits = ", ".join(f"{n}={params[n]['status']}" for n in names)
    return bits


def build_energy_ledger(region_polys, region_dong, priority_df, official_polys, params):
    rows = []
    s2_reports = {}

    for r, poly in region_polys.items():
        dong = region_dong[r]
        s1_length_m, s1_area_m2, s1_n_links = s1_priority_area(priority_df, r)
        s2 = s2_wide_existing_area(official_polys, poly, min_width=S2_MIN_WIDTH_M)
        s2_reports[r] = s2

        status_note = _param_status_note(
            params, ["canopy_density", "pv_cf", "grid_ef", "pv_embodied"]
        )
        set_specs = {
            "S1_priority": (
                s1_area_m2,
                s1_length_m,
                f"길이=gap_m 합({s1_n_links}개 링크), 캐노피 폭 {S1_WIDTH_M}m 가정(ASSUMPTION)",
            ),
            "S2_wide_existing": (
                s2["area_m2"],
                None,
                f"공식 면형의 지역 내 도형지수(2A/P)>={S2_MIN_WIDTH_M}m 필터(최소폭/설치가능면적 아님): "
                f"kept={s2['kept']}/total={s2['total']} (region과 교차하는 조각 기준)",
            ),
        }

        for set_name, (area_m2, length_m, set_note) in set_specs.items():
            for scenario in SCENARIOS:
                canopy = energy_rows(area_m2, params, scenario)
                base = {
                    "ledger": "energy",
                    "region": r,
                    "dong": dong,
                    "set": set_name,
                    "scenario": scenario,
                    "length_m": length_m,
                    "tree_check": TREE_CHECK_NOTE,
                }
                canopy_row = {
                    **base,
                    "site_type": "canopy",
                    **canopy,
                    "note": f"{set_note}; {BUSSTOP_NOTE}; {status_note}",
                }
                rows.append(canopy_row)

                factory_row = {
                    **base,
                    "site_type": "factory_roof",
                    **canopy,
                    "length_m": None,
                    "structure_embodied": STRUCTURE_FACTORY_ROOF,
                    "note": (
                        f"동일 capacity_kw({canopy['capacity_kw']:.1f}kW)의 공장 지붕 설치 비교; "
                        f"방위각 등 정량적 방위 차이는 미확보(ASSUMPTION: 캐노피와 동일 CF/grid_ef); "
                        f"{status_note}"
                    ),
                }
                rows.append(factory_row)

    energy_df = pd.DataFrame(rows)
    return energy_df, s2_reports


def build_transport_ledger(params):
    """`required_avoided_vkm`의 설명용 곡선표 -- car_ef_fleet(NOT_FOUND)
    없이, 대체 계수 2종(mid)으로 extra_emission_t별 필요 회피 vkm을 보여준다."""
    ef_specs = [
        (
            "car_ef_newcar_mid",
            get_scenario_value(params, "car_ef_newcar", "mid") / 1000.0,  # gCO2/km -> kg/vkm
            params["car_ef_newcar"]["status"],
        ),
        (
            "car_ef_uk_desnz_mid",
            get_scenario_value(params, "car_ef_uk_desnz", "mid"),  # 이미 kgCO2eq/km
            params["car_ef_uk_desnz"]["status"],
        ),
    ]
    rows = []
    for extra_t in TRANSPORT_EXTRA_EMISSIONS_T:
        for ef_name, ef_kg_per_vkm, status in ef_specs:
            vkm = required_avoided_vkm(float(extra_t), ef_kg_per_vkm)
            rows.append({
                "ledger": "transport",
                "extra_emission_t": extra_t,
                "ef_name": ef_name,
                "ef_kg_per_vkm": ef_kg_per_vkm,
                "ef_status": status,
                "required_avoided_vkm": vkm,
                "time_basis": "avoided vkm and extra emissions must use the same declared period",
                "gas_scope": "CO2" if ef_name == "car_ef_newcar_mid" else "CO2e",
                "is_observed_transport_reduction": False,
                "note": (
                    "국내 신차 CO2와 영국 차량 CO2e 대체계수는 별개 시나리오(한국 운행차 평균 미확보). 같은 가스 범위·기간의 추가배출을 가정한 설명용 손익분기점; 실측 절감량 "
                    f"아님; car_ef_fleet=NOT_FOUND; {ef_name} status={status}"
                ),
            })
    return pd.DataFrame(rows)


def run_sanity_checks(energy_df, s2_reports, params):
    mid_canopy = energy_df[
        (energy_df["scenario"] == "mid") & (energy_df["site_type"] == "canopy")
    ]
    mid_factory = energy_df[
        (energy_df["scenario"] == "mid") & (energy_df["site_type"] == "factory_roof")
    ]

    per_kw = (mid_canopy["energy_mwh_yr"] / mid_canopy["capacity_kw"]).round(6).unique()
    expected_per_kw = round(8.76 * params["pv_cf"]["mid"], 6)
    print(f"sanity: mid MWh/kW/yr = {list(per_kw)} (expect {expected_per_kw})")

    tco2_per_kw_canopy = (mid_canopy["avoided_tco2_yr"] / mid_canopy["capacity_kw"]).round(6)
    tco2_per_kw_factory = (mid_factory["avoided_tco2_yr"] / mid_factory["capacity_kw"]).round(6)
    match = sorted(tco2_per_kw_canopy.tolist()) == sorted(tco2_per_kw_factory.tolist())
    print(f"sanity: canopy vs factory-roof avoided tCO2/kW identical = {match}")

    for r, rep in s2_reports.items():
        print(f"sanity: region {r} S2 width(>= {S2_MIN_WIDTH_M}m) filter kept {rep['kept']}/{rep['total']}")

    ordering_ok = True
    for (r, set_name), sub in energy_df[energy_df["site_type"] == "canopy"].groupby(["region", "set"]):
        sub = sub.set_index("scenario")
        if not (sub.loc["low", "energy_mwh_yr"] <= sub.loc["mid", "energy_mwh_yr"] <= sub.loc["high", "energy_mwh_yr"]):
            ordering_ok = False
        if not (
            sub.loc["low", "avoided_tco2_yr"]
            <= sub.loc["mid", "avoided_tco2_yr"]
            <= sub.loc["high", "avoided_tco2_yr"]
        ):
            ordering_ok = False
    print(f"sanity: low<=mid<=high (energy_mwh_yr, avoided_tco2_yr) for every region x set = {ordering_ok}")


def cross_check_benchmark(region_polys, priority_df, s2_reports, mid_canopy, benchmark=BENCHMARK_MWH_PER_KM_YR):
    """mid 시나리오 S1+S2 MWh/km/yr (두 생활권 합) vs benchmark_sejong_mwh_per_km_yr(478).
    S2는 길이 개념이 없어 area/폭가정(3.0m)으로 역산한다 (ASSUMPTION, report에 명시)."""
    total_mwh = float(mid_canopy["energy_mwh_yr"].sum())
    total_km = 0.0
    for r in region_polys:
        s1_length_m, _, _ = s1_priority_area(priority_df, r)
        total_km += s1_length_m / 1000.0
        s2_area = s2_reports[r]["area_m2"]
        total_km += (s2_area / S2_MIN_WIDTH_M) / 1000.0
    if total_km <= 0:
        return float("nan"), float("nan"), total_km
    mwh_per_km = total_mwh / total_km
    ratio = mwh_per_km / benchmark
    return mwh_per_km, ratio, total_km


def write_numbers(numbers_path, region_polys, region_dong, energy_df, ratio):
    mid_canopy = energy_df[(energy_df["scenario"] == "mid") & (energy_df["site_type"] == "canopy")]
    for r in region_polys:
        for set_name in ("S1_priority", "S2_wide_existing"):
            row = mid_canopy[(mid_canopy["region"] == r) & (mid_canopy["set"] == set_name)]
            if row.empty:
                continue
            row = row.iloc[0]
            record(f"canopy_r{r}_{set_name}_kw_mid", round(float(row["capacity_kw"]), 2),
                   f"{region_dong[r]}, {set_name}; illustrative ASSUMPTION(canopy_density,pv_cf), not buildable area", path=numbers_path)
            record(f"canopy_r{r}_{set_name}_mwh_mid", round(float(row["energy_mwh_yr"]), 2),
                   f"{region_dong[r]}, {set_name}; SECONDARY(pv_cf)", path=numbers_path)
            record(f"canopy_r{r}_{set_name}_tco2_mid", round(float(row["avoided_tco2_yr"]), 2),
                   f"{region_dong[r]}, {set_name}; gross avoided CO2e, not lifecycle net; see params source status", path=numbers_path)
            payback = row["module_payback_yr"]
            record(
                f"canopy_r{r}_{set_name}_module_payback_mid",
                round(float(payback), 3) if pd.notna(payback) else "NA",
                f"{region_dong[r]}, {set_name}; SECONDARY(pv_embodied); 강구조 회수연수는 "
                f"NOT_FOUND(canopy_steel_mass) -- 미산정",
                path=numbers_path,
            )

    record(
        "canopy_benchmark_ratio_mid",
        round(float(ratio), 4) if ratio == ratio else "NA",
        f"입력 가정의 단위 일관성 점검일 뿐 독립 현장검증 아님(면적 상쇄); S1/S2와 두 생활권 합산 효과로 해석 금지; "
        f"(SECONDARY, 세종-대전 실적); S2 길이는 area/{S2_MIN_WIDTH_M}m 가정으로 역산(ASSUMPTION)",
        path=numbers_path,
    )


def run(repo_root: Path, dong_names: list[str]):
    repo_root = Path(repo_root)
    region_polygons_fn = _load_region_polygons_fn()
    region_polys = region_polygons_fn(repo_root, dong_names)
    region_dong = dict(enumerate(dong_names, start=1))

    params = load_params(repo_root / PARAMS_REL)
    priority_df = pd.read_csv(repo_root / PRIORITY_LINKS_REL)
    official_polys = load_official_polygons(repo_root)

    energy_df, s2_reports = build_energy_ledger(
        region_polys, region_dong, priority_df, official_polys, params
    )
    transport_df = build_transport_ledger(params)

    # --- no-sum guard (controller ruling: two ledgers never summed) -------
    assert_no_sum_columns(energy_df)
    assert_no_sum_columns(transport_df)
    assert set(energy_df["ledger"]) == {"energy"}
    assert set(transport_df["ledger"]) == {"transport"}

    energy_path = repo_root / "outputs" / "table2_energy_ledger.csv"
    transport_path = repo_root / "outputs" / "table2_transport_ledger.csv"
    energy_path.parent.mkdir(parents=True, exist_ok=True)
    energy_df.to_csv(energy_path, index=False, encoding="utf-8-sig")
    transport_df.to_csv(transport_path, index=False, encoding="utf-8-sig")
    print(f"wrote {energy_path} ({len(energy_df)} rows)")
    print(f"wrote {transport_path} ({len(transport_df)} rows)")

    run_sanity_checks(energy_df, s2_reports, params)

    mid_canopy = energy_df[(energy_df["scenario"] == "mid") & (energy_df["site_type"] == "canopy")]
    mwh_per_km, ratio, total_km = cross_check_benchmark(region_polys, priority_df, s2_reports, mid_canopy,
        get_scenario_value(params, "benchmark_sejong_mwh_per_km_yr", "mid"))
    print(
        f"sanity/cross-check: mid S1+S2 (두 생활권 합) MWh/km/yr = {mwh_per_km:.1f} "
        f"(total_km={total_km:.3f}), benchmark={BENCHMARK_MWH_PER_KM_YR}, ratio={ratio:.3f}"
    )

    numbers_path = repo_root / "outputs" / "numbers.csv"
    write_numbers(numbers_path, region_polys, region_dong, energy_df, ratio)

    print(transport_df.to_string(index=False))
    return energy_df, transport_df


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path, default=config.ROOT)
    ap.add_argument("--regions", required=True, help="정확히 2개, 콤마로 구분한 읍면동 이름")
    args = ap.parse_args()

    dong_names = [s.strip() for s in args.regions.split(",")]
    if len(dong_names) != 2:
        raise SystemExit(f"--regions는 정확히 2개의 동이어야 함: {dong_names}")

    run(args.repo_root, dong_names)


if __name__ == "__main__":
    main()
