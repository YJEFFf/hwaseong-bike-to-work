"""에너지 장부(차양막 PV)·교통 장부(설명용 수식) 계산.

두 장부는 절대 합산하지 않는다 (task-15b-brief.md). 이 모듈은 순수 계산
함수만 제공한다 -- 지오메트리 I/O와 표 조립은 scripts/06_carbon.py가 한다.

- `load_params`: params/carbon_params.csv (param,low,mid,high,unit,source,
  url,status,note)를 param -> dict로 읽는다. low/mid/high가 빈 문자열이면
  None (NOT_FOUND 행 -- 로드 시점에는 에러 내지 않는다).
- `get_scenario_value`: 계산에 실제로 쓰려는 시점에 None이면 그 param 이름을
  포함한 ValueError를 낸다 (NOT_FOUND 값을 조용히 계산에 흘려보내지 않기
  위한 게이트).
- `strip_width`: 얇은 띠 폴리곤의 폭 근사값 2*area/perimeter.
- `energy_rows`: 면적(m²) 하나에 대해 시나리오(low/mid/high) 하나의 캐노피
  PV 에너지 장부 행 하나를 만든다. embodied는 시나리오와 반대 방향으로
  매핑한다 (low 시나리오 = 비관적 = high embodied, high 시나리오 = 낙관적
  = low embodied -- brief 표 참고).
- `s2_wide_existing_area`: 폭 필터(>=min_width)를 통과한 폴리곤들의
  region-클립 면적 합과 kept/total 카운트.
- `assert_no_sum_columns`: 두 장부(에너지/교통)를 합산한 열이 실수로
  섞여 들어오지 않도록 하는 가드.
"""
import csv
import math
from shapely.ops import unary_union

from analysis.carbon import payback_years, pv_capacity_kw, pv_energy_mwh

# 시나리오(전체 낙관/비관 방향) -> embodied 컬럼. embodied는 반대 방향:
# low(비관) 시나리오는 임베디드 탄소가 높다고 가정 (high embodied),
# high(낙관) 시나리오는 임베디드 탄소가 낮다고 가정 (low embodied).
_EMBODIED_SCENARIO = {"low": "high", "mid": "mid", "high": "low"}

STRUCTURE_NOT_COSTED = "미산정(canopy_steel_mass NOT_FOUND)"
STRUCTURE_FACTORY_ROOF = "기존 지붕 활용 가정; 지붕 보강·구조·방수 공사 미산정"


def _to_float(raw):
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None
    return float(s)


def load_params(path) -> dict:
    """params/carbon_params.csv -> {param: {low, mid, high, unit, source,
    url, status, note}}. NOT_FOUND 행은 low/mid/high가 모두 None."""
    params = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            name = row["param"]
            if name in params:
                raise ValueError(f"duplicate parameter: {name}")
            params[name] = {
                "low": _to_float(row.get("low")),
                "mid": _to_float(row.get("mid")),
                "high": _to_float(row.get("high")),
                "unit": row.get("unit", ""),
                "source": row.get("source", ""),
                "url": row.get("url", ""),
                "status": row.get("status", ""),
                "note": row.get("note", ""),
            }
    return params


def get_scenario_value(params: dict, name: str, scenario: str) -> float:
    """params[name][scenario]를 반환하고, None이면(NOT_FOUND 등) 그 param
    이름을 포함한 ValueError를 낸다."""
    if name not in params:
        raise ValueError(f"unknown param: '{name}'")
    entry = params[name]
    value = entry.get(scenario)
    if value is None or not math.isfinite(value):
        status = entry.get("status", "?")
        raise ValueError(
            f"param '{name}' has no '{scenario}' value (status={status}) "
            f"-- cannot use for computation"
        )
    return value


def strip_width(polygon) -> float:
    """Shape index 2A/P, NOT minimum usable width or installation clearance."""
    area = polygon.area
    perimeter = polygon.length
    if perimeter <= 0:
        raise ValueError("polygon has zero perimeter")
    return 2.0 * area / perimeter


def energy_rows(area_m2: float, params: dict, scenario: str) -> dict:
    """면적(m²) 하나 + 시나리오(low/mid/high) 하나의 캐노피 PV 에너지 장부 행.

    scenario는 canopy_density/pv_cf/grid_ef에는 그대로, pv_embodied에는
    반대 방향으로 매핑된다 (모듈 상단 문서 참고).
    """
    if scenario not in ("low", "mid", "high"):
        raise ValueError(f"scenario must be one of low/mid/high, got {scenario!r}")

    embodied_scenario = _EMBODIED_SCENARIO[scenario]
    density = get_scenario_value(params, "canopy_density", scenario)
    cf = get_scenario_value(params, "pv_cf", scenario)
    grid_ef = get_scenario_value(params, "grid_ef", scenario)
    embodied = get_scenario_value(params, "pv_embodied", embodied_scenario)

    if not math.isfinite(area_m2) or area_m2 < 0 or density <= 0 or not 0 < cf <= 1 or grid_ef <= 0 or embodied < 0:
        raise ValueError("invalid area/density/CF/emission coefficient")
    capacity_kw = pv_capacity_kw(area_m2, density)
    energy_mwh_yr = pv_energy_mwh(capacity_kw, cf)
    avoided_tco2_yr = energy_mwh_yr * grid_ef
    pv_module_embodied_t = capacity_kw * embodied / 1000.0  # kgCO2e/kWp*kW -> tCO2e
    module_payback_yr = (
        payback_years(pv_module_embodied_t, avoided_tco2_yr) if avoided_tco2_yr > 0 else None
    )

    return {
        "area_m2": area_m2,
        "scenario": scenario,
        "capacity_kw": capacity_kw,
        "energy_mwh_yr": energy_mwh_yr,
        "avoided_tco2_yr": avoided_tco2_yr,  # legacy column alias, gross CO2e only
        "gross_grid_avoided_tco2e_yr": avoided_tco2_yr,
        "time_basis": "annual generation at assumed constant CF",
        "carbon_scope": "gross grid displacement; not lifecycle net; modules only in payback",
        "siting_status": "unverified illustrative area; not buildable area",
        "transport_additionality": "not estimated",
        "pv_module_embodied_t": pv_module_embodied_t,
        "module_payback_yr": module_payback_yr,
        "structure_embodied": STRUCTURE_NOT_COSTED,
    }


def s2_wide_existing_area(polygons, region_poly, min_width: float = 3.0) -> dict:
    """폭(strip_width) >= min_width인 폴리곤만 골라 region에 클립한 면적 합.

    polygons: 단일(Polygon) 조각들의 iterable (MultiPolygon은 호출부가
    .geoms로 미리 풀어서 넘긴다 -- 폭 근사는 조각 단위로만 의미가 있다).
    반환: area_m2(클립된 면적 합), kept(폭 필터 통과 개수), total(교차한
    폴리곤 총 개수).
    """
    total = 0
    kept = 0
    clipped = []
    for poly in polygons:
        if not poly.intersects(region_poly):
            continue
        total += 1
        part = poly.intersection(region_poly)
        if part.is_empty or part.area <= 0:
            continue
        width = strip_width(part)
        if width >= min_width:
            kept += 1
            clipped.append(part)
    area_m2 = unary_union(clipped).area if clipped else 0.
    return {"area_m2": area_m2, "kept": kept, "total": total,
            "meaning": "deduplicated clipped shape-index-selected area; NOT minimum width or buildable area"}


def assert_no_sum_columns(df) -> None:
    """열 이름에 "total"이나 "합계"가 있으면 AssertionError -- 두 장부가
    실수로 합산된 표를 조기에 잡기 위한 가드."""
    for col in df.columns:
        col_lower = str(col).lower()
        assert "total" not in col_lower, f"forbidden aggregate column: {col!r}"
        assert "합계" not in str(col), f"forbidden aggregate column: {col!r}"
