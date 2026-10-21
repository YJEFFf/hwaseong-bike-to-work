"""자전거 접근성(누적기회) 계산.

목적지 집합에서 다중시작점(multi-source) dijkstra를 한 번 돌려 모든 노드까지
가장 가까운 목적지까지의 거리를 구한 뒤, origin이 스냅된 노드의 그 거리가
cutoff 이내인지로 도달 여부를 판정한다. origin마다 개별 dijkstra를 도는 것보다
훨씬 빠르다 (수만 개 인구 셀 x 여러 버전/kind/cutoff 조합을 감당하기 위함).
동등성은 tests/test_access.py의 brute-force(원점별 dijkstra) 비교로 검증한다.

`dest_node_distances` / `reach_mask_from_distances`는 `popshare_reaching`이
내부적으로 쓰는 두 단계(거리 계산, cutoff 문턱값 비교)를 분리해 노출한 것으로,
호출부(scripts/04_access.py)가 같은 (version, kind)에 대해 여러 cutoff를 비교할
때 dijkstra를 cutoff 개수만큼 반복하지 않고 한 번(가장 큰 cutoff로 limit)만
돌리고 재사용할 수 있게 한다.
"""
import json
from pathlib import Path

import numpy as np
from pyproj import Transformer
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import LineString, shape

from analysis.network import nearest_nodes
from analysis.osm_filter import is_bike_facility
from analysis.osm_roads import load_low_stress_lines
from analysis.topo import dedupe

# union/lowstress 망 정의(Task 12c fix round 1, scripts/04_access.py에서 이동
# -- Task 13b controller ruling 2). 상대경로는 --repo-root 기준.
OFFICIAL_LINES_REL = "data/raw/화성_자전거노선_5186.geojson"
OSM_BIKE_REL = "data/raw/osm_hwaseong_bike_20260914.json"
LOWSTRESS_ROADS_REL = "data/raw/osm_roads/roads.json"

_DISTRICT_BY_SGG = {
    "화성시 동탄구": "dongtan",
    "화성시 병점구": "byeongjeom",
    "화성시 효행구": "hyohaeng",
    "화성시 만세구": "manse",
}


def district_of(sgg: str) -> str:
    """시군구명(예: '화성시 동탄구') -> district 키. 매핑에 없으면 'other'."""
    return _DISTRICT_BY_SGG.get(sgg, "other")


def dong_district_map(pop_df) -> dict:
    """인구 CSV(시군구명, 읍면동명 열 포함)에서 읍면동명 -> district 매핑을 만든다.

    - 시군구명이 '화성시'로 시작하는 행만 쓴다 (다른 시/도의 동명이인 읍면동과
      섞이지 않도록 화성시로 범위를 좁힌다).
    - 읍면동명이 비어 있는 행(구/시 집계행)은 제외한다 (Task 11 브리프와 동일한
      관례: "29 읍면동 rows plus possible 구/시 aggregate rows (읍면동명 empty) —
      keep only rows with non-empty 읍면동명").
    - 값은 district_of(시군구명)이다. 화성시 산하 4개 구(동탄/병점/효행/만세) 중
      하나가 아닌 시군구명이 남아 있다면(현재 화성시 관할구는 이 4개뿐이라 실제로는
      발생하지 않지만) 'other'로 남는다 — 매핑에 아예 없는 읍면동명과는 다른
      경우다: 그런 행은 이 함수가 읽은 원본 CSV 안에서 시군구명 자체가 확인됐다는
      뜻이므로, assign_districts()가 다루는 "원본 CSV에 없는 dong 이름" 오류와는
      구분한다.
    """
    hs = pop_df[pop_df["시군구명"].astype(str).str.startswith("화성시")]
    dong = hs["읍면동명"]
    non_empty = dong.notna() & (dong.astype(str).str.strip() != "")
    hs = hs[non_empty]
    return {d: district_of(sgg) for d, sgg in zip(hs["읍면동명"], hs["시군구명"])}


def assign_districts(dongs, mapping: dict) -> list:
    """dongs(반복 가능한 dong 이름들)를 mapping(dong_district_map 결과)으로 변환.

    mapping에 없는 dong 이름이 하나라도 있으면, 그 전부를 나열한 ValueError를
    던진다 — 이름 표기 불일치(공백, 접미사 등)를 'other'로 조용히 감추지 않고
    바로 드러내기 위함. 'other'는 매핑에 있지만 4개 구 밖인 경우에만 쓴다
    (dong_district_map 참고).
    """
    result = []
    unmatched = []
    for d in dongs:
        if d in mapping:
            result.append(mapping[d])
        else:
            unmatched.append(d)
    if unmatched:
        names = sorted(set(unmatched))
        raise ValueError(f"unmatched dong names (not in population CSV mapping): {names}")
    return result


def dest_node_distances(G, nodes_xy, dests_df, limit, snap_max=150.0):
    """모든 노드에서 가장 가까운 목적지까지의 거리(다중시작점 dijkstra, limit 이내).

    dests_df가 비어 있거나 전부 스냅 실패하면 모든 노드에 대해 inf 배열을 반환한다.
    limit은 이후 이 결과를 threshold할 cutoff들의 최댓값 이상이어야 한다: limit
    이내 거리는 정확한 최단거리이므로, 더 작은 cutoff와 비교해도 별도로
    limit=cutoff로 다시 돌린 것과 같은 결과가 나온다 (reach_mask_from_distances).
    """
    n_nodes = len(nodes_xy)
    if len(dests_df) == 0:
        return np.full(n_nodes, np.inf)

    dest_xy = dests_df[["x", "y"]].to_numpy(dtype=float)
    d_idx = nearest_nodes(nodes_xy, dest_xy, snap_max)
    dest_nodes = np.unique(d_idx[d_idx >= 0])
    if dest_nodes.size == 0:
        return np.full(n_nodes, np.inf)

    return dijkstra(G, directed=False, indices=dest_nodes, limit=limit, min_only=True)


def reach_mask_from_distances(nodes_xy, origins_df, dist_from_nearest_dest, cutoff, snap_max=150.0):
    """dest_node_distances 결과를 cutoff로 threshold해 (인구 비율, origin별 도달
    mask)를 반환한다. popshare_reaching과 동일한 규칙: snap_max 밖 origin은
    미도달 처리, 총인구 0이면 (0.0, 전부 False).
    """
    n_origins = len(origins_df)
    mask = np.zeros(n_origins, dtype=bool)

    pop = origins_df["pop"].to_numpy(dtype=float)
    total_pop = pop.sum()
    if total_pop == 0:
        return 0.0, mask

    origin_xy = origins_df[["x", "y"]].to_numpy(dtype=float)
    o_idx = nearest_nodes(nodes_xy, origin_xy, snap_max)
    matched = o_idx >= 0
    mask[matched] = dist_from_nearest_dest[o_idx[matched]] <= cutoff

    share = float(pop[mask].sum() / total_pop)
    return share, mask


def unsnapped_mask(nodes_xy, origins_df, snap_max=150.0) -> np.ndarray:
    """origin이 이 망(nodes_xy)의 어떤 노드에도 snap_max 이내로 스냅되지
    않는지("detached") 표시하는 boolean mask (final-review I3). 목적지나
    cutoff와 무관 -- 망(버전)에만 의존한다: 시설이 전혀 없는 곳에 사는
    origin은 어떤 cutoff를 골라도, 어떤 링크를 추가해도 "도달"할 수 없다는
    뜻이므로 "스냅은 됐지만 cutoff를 넘는" origin과 구분해서 셀 수 있어야
    한다(access_{version}_{kind}_c2500_pop_detached vs
    _pop_attached_unreached).
    """
    origin_xy = origins_df[["x", "y"]].to_numpy(dtype=float)
    idx = nearest_nodes(nodes_xy, origin_xy, snap_max)
    return idx < 0


def popshare_reaching(G, nodes_xy, origins_df, dests_df, cutoff, snap_max=150.0):
    """origins_df 인구 중 cutoff(m) 이내 네트워크 경로로 dests_df 중 하나 이상에
    도달 가능한 인구의 비율과, origin별 도달 여부 boolean mask를 반환한다.

    origins_df: x, y, pop 열을 가진 DataFrame (population 셀, EPSG:5186).
    dests_df: x, y 열을 가진 DataFrame (목적지, EPSG:5186).
    snap_max보다 네트워크에서 먼 origin/dest는 스냅되지 않아(-1) 도달 불가로 처리된다.
    dests_df가 비어 있거나(또는 전부 스냅 실패) 총인구가 0이면 (0.0, 전부 False)를 반환한다.

    단일 cutoff용 편의 함수: dest_node_distances(limit=cutoff) +
    reach_mask_from_distances(cutoff)를 합친 것과 같다. 여러 cutoff를 같은
    (version, kind)에 대해 비교해야 한다면 dijkstra를 cutoff 개수만큼 반복하지
    않도록 dest_node_distances/reach_mask_from_distances를 직접 쓴다.

    Returns: (share: float, mask: np.ndarray[bool] of len(origins_df))
    """
    if len(dests_df) == 0 or origins_df["pop"].to_numpy(dtype=float).sum() == 0:
        return 0.0, np.zeros(len(origins_df), dtype=bool)

    dist_from_nearest_dest = dest_node_distances(G, nodes_xy, dests_df, limit=cutoff, snap_max=snap_max)
    return reach_mask_from_distances(nodes_xy, origins_df, dist_from_nearest_dest, cutoff, snap_max=snap_max)


# --- union/lowstress network builders -------------------------------------
# scripts/04_access.py에 있던 로더들을 그대로 옮긴 것 (Task 13b controller
# ruling 2: 동작 동일, scripts/04_access.py는 이 함수들을 호출하도록 바뀐다).

def explode_to_lines(fc: dict) -> list:
    lines = []
    for feat in fc["features"]:
        geom = shape(feat["geometry"])
        if geom.geom_type == "MultiLineString":
            lines.extend(list(geom.geoms))
        elif geom.geom_type == "LineString":
            lines.append(geom)
        else:
            raise ValueError(f"unexpected geometry type: {geom.geom_type}")
    return lines


def load_official_lines(repo_root) -> list:
    """공식 노선 전체를 explode + 중복(hausdorff<2m) 제거한 LineString 목록."""
    from analysis.io_canon import load_fc

    fc = load_fc(Path(repo_root) / OFFICIAL_LINES_REL)
    all_lines = explode_to_lines(fc)
    keep_idx = dedupe(all_lines)
    return [all_lines[i] for i in keep_idx]


def load_osm_lines_5186(repo_root) -> list:
    """is_bike_facility 필터를 통과한 OSM way를 WGS84 -> EPSG:5186으로 변환."""
    with open(Path(repo_root) / OSM_BIKE_REL, encoding="utf-8") as f:
        data = json.load(f)

    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True)
    lines = []
    for el in data.get("elements", []):
        if el.get("type") != "way" or not is_bike_facility(el.get("tags", {})):
            continue
        geometry = el.get("geometry") or []
        if len(geometry) < 2:
            continue
        coords = [transformer.transform(pt["lon"], pt["lat"]) for pt in geometry]
        lines.append(LineString(coords))
    return lines


def union_lines(repo_root) -> list:
    """union 망 = official 전체(중복제거) + osm 전체(is_bike_facility 통과)의
    실제 합집합 (Task 12c fix round 1 정의). bridge_components는 포함하지
    않는다 -- 그래프를 만든 뒤 analysis.network.bridge_components(tol=10.0)를
    별도로 적용한다 (호출부: scripts/04_access.py, scripts/05_priority.py)."""
    return load_official_lines(repo_root) + load_osm_lines_5186(repo_root)


def lowstress_lines(repo_root) -> list:
    """lowstress 망 = 저위계 허용망(load_low_stress_lines) + union_lines
    (Task 12c 정의). union과 마찬가지로 bridge_components는 그래프 빌드 후
    호출부가 별도로 적용한다."""
    return load_low_stress_lines(Path(repo_root) / LOWSTRESS_ROADS_REL) + union_lines(repo_root)
