"""공식 자전거 노선(화성_자전거노선_5186.geojson) 위상 분석.

사용법: python scripts/01c_official_topology.py

- MultiLineString 피처를 LineString 파트로 explode
- 중복(허프도프 거리 2m 미만) 라인 제거 개수 보고
- 매달린 끝점(dangling endpoint) 수·전체 끝점 수·knee 거리는 모두 **원본
  (중복제거 이전, explode만 한) 라인 집합**을 단일 기준(canonical population)으로
  계산해 기록한다. 중복제거 집합 기준 수치는 참고용 진단(diagnostic)으로만
  출력하고 기록하지 않는다 — 두 지표가 서로 다른 모집단에서 나오는 것을 막기 위함.
- 매달린 끝점 -> 최근접 타 라인 거리의 급변점(knee) 계산 (원본 집합 기준)
- tol=1m 허용오차에서 최대 연결 성분 비율을, 추가 엣지(같은 BYC003/BYC004
  교차부 폴리곤 3m 이내에 걸리는 라인들을 서로 연결) 없이/있이 각각 계산
  (이 두 지표는 중복제거 집합 기준 — 성분 길이 집계에서 중복 라인이 길이를
  이중 계산하지 않도록)

결과는 outputs/numbers.csv 에 기록한다.
"""
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapely import STRtree
from shapely.geometry import shape

from analysis import config
from analysis.io_canon import load_fc
from analysis.numbers import record
from analysis.topo import (
    components,
    dangling_endpoints,
    dangling_endpoints_with_source,
    dedupe,
    knee_distance,
)

CROSSING_CODES = {"BYC003", "BYC004"}
KNEE_GRID = [1, 2, 5, 10, 15, 20, 30, 50, 100]
EXPECTED_DANGLING = 527
EXPECTED_TOTAL = 562


def explode_to_lines(fc: dict) -> list:
    """FeatureCollection의 (Multi)LineString 피처를 LineString 파트 리스트로 explode."""
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


def nearest_other_line_distance(point, source_idx, lines) -> float:
    return min(g.distance(point) for j, g in enumerate(lines) if j != source_idx)


def crossing_extra_edges(lines, polys_fc, buffer_m: float = 3.0) -> list:
    """같은 BYC003/BYC004 교차부 폴리곤 buffer_m 이내에 걸리는 라인들을 서로 잇는 엣지."""
    line_tree = STRtree(lines)
    edges = set()
    for feat in polys_fc["features"]:
        if feat["properties"].get("byc_cde") not in CROSSING_CODES:
            continue
        poly = shape(feat["geometry"])
        hits = [int(j) for j in line_tree.query(poly, predicate="dwithin", distance=buffer_m)]
        for i, j in itertools.combinations(sorted(set(hits)), 2):
            edges.add((i, j))
    return sorted(edges)


def main() -> None:
    lines_fc = load_fc(config.OFFICIAL_LINES)
    polys_fc = load_fc(config.OFFICIAL_POLYS)

    all_lines = explode_to_lines(lines_fc)

    keep_idx = dedupe(all_lines)
    duplicates_removed = len(all_lines) - len(keep_idx)
    dedup_lines = [all_lines[i] for i in keep_idx]

    # 정본(canonical) 모집단: 원본(중복제거 이전) 라인 집합.
    # dangling 개수·전체 끝점 수·knee 거리는 전부 이 하나의 모집단에서 계산해
    # 기록한다 (한 지표는 원본, 다른 지표는 중복제거 집합에서 나오는 불일치를 방지).
    endpoints_total = 2 * len(all_lines)
    dangling_indexed = dangling_endpoints_with_source(all_lines)
    n_dangling = len(dangling_indexed)

    dists = [nearest_other_line_distance(p, i, all_lines) for p, i in dangling_indexed]
    knee = knee_distance(dists, KNEE_GRID) if dists else float("nan")

    # 진단용(참고 출력만, 기록하지 않음): 중복제거 집합 기준으로도 계산했을 때
    # 얼마나 달라지는지 확인.
    n_dangling_dedup_diag = len(dangling_endpoints(dedup_lines))
    endpoints_total_dedup_diag = 2 * len(dedup_lines)

    lcc = components(dedup_lines, [], tol=1.0)
    total_len = sum(lcc)
    lcc_share = lcc[0] / total_len if total_len else float("nan")

    extra_edges = crossing_extra_edges(dedup_lines, polys_fc, buffer_m=3.0)
    lcc_xing = components(dedup_lines, extra_edges, tol=1.0)
    lcc_share_xing = lcc_xing[0] / total_len if total_len else float("nan")

    matches_expected = (n_dangling == EXPECTED_DANGLING and endpoints_total == EXPECTED_TOTAL)

    print(f"lines exploded: {len(all_lines)}")
    print(f"duplicates removed (hausdorff<2m): {duplicates_removed} (kept {len(dedup_lines)})")
    print(
        f"dangling endpoints (CANONICAL, original set): {n_dangling}/{endpoints_total} "
        f"(expected {EXPECTED_DANGLING}/{EXPECTED_TOTAL}, matches: {matches_expected})"
    )
    print(
        f"dangling endpoints (diagnostic only, de-duplicated set): "
        f"{n_dangling_dedup_diag}/{endpoints_total_dedup_diag} -- not recorded"
    )
    print(f"knee distance over grid {KNEE_GRID} (canonical, original set): {knee} m (expected 10)")
    print(f"LCC share @tol=1m, no extra edges: {lcc_share:.4f}")
    print(f"LCC share @tol=1m, with BYC003/004 crossing edges (<=3m, {len(extra_edges)} edges): {lcc_share_xing:.4f}")

    record(
        "official_duplicates_removed",
        duplicates_removed,
        "hausdorff<2m 중복 라인 제거 개수 (기대: 3쌍)",
    )
    record(
        "official_dangling_endpoints",
        n_dangling,
        "매달린 끝점 수 (원본/중복제거전 집합 기준, 기대 527/562)",
    )
    record(
        "official_endpoints_total",
        endpoints_total,
        "전체 끝점 수 (원본/중복제거전 집합 기준)",
    )
    record(
        "official_knee_m",
        knee,
        f"매달린 끝점(원본 집합) -> 최근접 타 라인 거리 급변점, grid={KNEE_GRID} (기대 10)",
    )
    record(
        "official_lcc_share_tol1",
        round(lcc_share, 4),
        "tol=1m, 추가 엣지 없음, 최대 연결 성분 길이 비율",
    )
    record(
        "official_lcc_share_tol1_xing",
        round(lcc_share_xing, 4),
        "tol=1m, BYC003/004 교차부 3m 이내 접속 포함, 최대 연결 성분 길이 비율",
    )


if __name__ == "__main__":
    main()
