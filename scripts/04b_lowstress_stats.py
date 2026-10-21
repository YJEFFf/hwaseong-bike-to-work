"""저위계 도로 허용망(low-stress network) 통계 (Task 12b).

사용법: python scripts/04b_lowstress_stats.py [--repo-root PATH]

Task 12(자전거 시설 전용망)와 별도로, 자전거 시설 + 자전거로 이용 가능한
OSM 소로(analysis.osm_roads.load_low_stress_lines)로 그래프를 만들어
망의 규모와 연결성을 기록한다. scripts/04_access.py는 Task 12c에서 이
loader를 호출해 lowstress 버전(저위계 허용망 + union)을 추가했다
(final-review M5: 예전 docstring은 "건드리지 않는다"고 적어 이미 배선이
끝난 상태와 어긋났다 -- analysis.access.lowstress_lines 참고).

원자료(data/raw/osm_roads/roads.json)가 이 워크트리에는 없으므로, 실제
실행은 --repo-root 로 원자료와 outputs/numbers.csv 가 있는 메인 체크아웃
경로를 넘겨서 한다. 기본값은 analysis.config.ROOT(이 저장소 자신).

outputs/numbers.csv 에 기록:
- lowstress_ways: 저위계 허용망에 포함된 way 수
- lowstress_km: 총 연장(km, EPSG:5186 투영 길이 합 / 1000, 소수 1자리)
- lowstress_nodes: build_graph(snap=1.0) 그래프의 노드 수
- lowstress_lcc_share: 최대 연결요소(LCC)의 엣지 길이 합이 전체 엣지 길이
  합에서 차지하는 비율 (scipy.sparse.csgraph.connected_components)
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from scipy.sparse.csgraph import connected_components

from analysis import config
from analysis.network import build_graph
from analysis.numbers import record
from analysis.osm_roads import load_low_stress_lines


def lcc_share(G) -> float:
    """그래프 G(무방향, 대칭 csr)에서 최대 연결요소의 엣지 길이 합 비율."""
    n_components, labels = connected_components(G, directed=False)

    coo = G.tocoo()
    # G는 각 무방향 엣지를 (i, j)와 (j, i) 양방향으로 담고 있다 (network.build_graph
    # 참고). row < col인 항목만 세면 엣지 하나당 한 번씩만 더하게 된다.
    upper = coo.row < coo.col
    if not upper.any():
        return 0.0

    edge_len = coo.data[upper]
    comp_of_edge = labels[coo.row[upper]]

    total = edge_len.sum()
    if total == 0:
        return 0.0

    comp_totals = np.bincount(comp_of_edge, weights=edge_len, minlength=n_components)
    return float(comp_totals.max() / total)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root", type=Path, default=config.ROOT,
        help="data/raw/osm_roads/roads.json 및 outputs/ 가 있는 루트 (기본값: 이 저장소)",
    )
    args = parser.parse_args()

    repo_root: Path = args.repo_root
    roads_path = repo_root / "data" / "raw" / "osm_roads" / "roads.json"
    numbers_path = repo_root / "outputs" / "numbers.csv"

    lines = load_low_stress_lines(roads_path)
    G, nodes_xy = build_graph(lines, snap=1.0)

    stats = {
        "lowstress_ways": len(lines),
        "lowstress_km": round(sum(line.length for line in lines) / 1000, 1),
        "lowstress_nodes": len(nodes_xy),
        "lowstress_lcc_share": round(lcc_share(G), 4),
    }

    record("lowstress_ways", stats["lowstress_ways"],
           "저위계 허용망(자전거 시설+소로) way 수 (Task 12b)", path=numbers_path)
    record("lowstress_km", stats["lowstress_km"],
           "저위계 허용망 총 연장(km, EPSG:5186 투영 길이)", path=numbers_path)
    record("lowstress_nodes", stats["lowstress_nodes"],
           "저위계 허용망 그래프 노드 수 (build_graph snap=1.0)", path=numbers_path)
    record("lowstress_lcc_share", stats["lowstress_lcc_share"],
           "최대 연결요소(LCC) 엣지 길이 비율", path=numbers_path)

    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
