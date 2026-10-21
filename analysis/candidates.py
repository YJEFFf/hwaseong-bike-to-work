"""Task 13b: 연결 후보 생성과 링크 하나의 한계 편익(marginal benefit).

- `dangling_candidates`: 망(G)의 차수-1(끝점, dangling) 노드 중 한 생활권
  폴리곤 안에 있는 것들을 대상으로, 서로 gap_m <= max_gap 이내인 쌍을
  후보로 만든다. 다른 연결요소면 무조건 후보, 같은 연결요소면 기존
  네트워크 경로가 직선 연결보다 훨씬 우회(detour)할 때만 후보로 남긴다
  (판정 기준은 max(detour_factor*gap, gap+detour_add)).
- `link_benefit`: 링크 (u, v) 하나를 추가했을 때 origin별로 "새로
  cutoff 이내에 도달하게 되는지" 판정한다. 전체 그래프를 다시 만들어
  dijkstra를 돌리지 않고, 링크 추가 전 원본 그래프에서 한 번 계산해 둔
  d0(모든 노드 -> 최근접 목적지)와 du/dv(u/v로부터의 거리)만으로
  d1[n] = min(d0[n], du[n]+L+d0[v], dv[n]+L+d0[u])를 계산한다(브리프
  Step 5 공식). "benefit"은 한계(marginal) 편익이므로 이미 도달하던
  origin은 다시 세지 않는다.
- `bottom_share_mask`: 기준 거리(d0)가 가장 먼(inf 포함) origin부터
  인구를 누적해 전체 인구의 share(기본 20%)에 도달할 때까지(도달시킨
  origin 포함) 표시하는 mask -- rule `bottom20`이 쓰는 "하위 20%" 정의.
  정렬은 결정적이다: d0 내림차순(inf 먼저) -> `tiebreak_desc` 내림차순
  -> `stable_id` 오름차순(final-review C1 -- 행 순서에 의존하던 이전
  버전의 stable-sort tie-break를 명시적 키로 교체).
- `stable_origin_rank`: (x, y) 좌표만으로 만든, 행 순서와 무관한 정수
  순위(final-review C1의 "map back by stable id" 요구 충족).
- `distinct_reach_pop`: 여러 후보 링크의 신규도달 mask들을 OR로 합친 뒤
  (중복 인구 제거) 인구를 합산한다 -- final-review C2, "top-N 집합의
  편익 = 합이 아니라 distinct 인구".
- `tie_inclusive_topn`: N등 점수와 동점인 후보까지 전부 포함하는 top-N
  집합을 만든다(final-review I5). 표시 순서는 gap 오름차순 -> cand_id
  오름차순.
"""
import numpy as np
import pandas as pd
import shapely
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree


def dangling_candidates(G, nodes_xy, region_polys, max_gap, detour_factor=3.0, detour_add=200.0):
    """생활권(region_polys)별 연결 후보 DataFrame.

    region_polys: {region_label: shapely Polygon} (이미 버퍼/클립된 생활권
    경계 -- scripts/05_priority.py 참고). 생활권마다 독립적으로 스캔한다.

    두 dangling 노드(차수 1) i, j가 같은 region 안에 있고 유클리드 gap이
    max_gap 이하일 때 다음 중 하나면 후보:
      - 서로 다른 연결요소, 또는
      - 같은 연결요소지만 기존 네트워크 거리가
        max(detour_factor * gap, gap + detour_add)를 초과 ("detour rule").

    반환 컬럼: cand_id, region, gap_m, x1, y1, x2, y2. cand_id는
    (region, gap_m, x1, y1, x2, y2) 정렬 순서의 0부터의 행 위치로 고정한다
    (브리프: "Candidate ID fixed from this universe").
    """
    nodes_xy = np.asarray(nodes_xy, dtype=float)[:, :2]
    degree = np.asarray((G != 0).sum(axis=1)).ravel()
    dangling = np.where(degree == 1)[0]
    _, labels = connected_components(G, directed=False)

    rows = []
    for region, poly in region_polys.items():
        if dangling.size < 2:
            continue
        in_poly = shapely.contains_xy(poly, nodes_xy[dangling, 0], nodes_xy[dangling, 1])
        region_nodes = dangling[in_poly]
        if region_nodes.size < 2:
            continue

        tree = cKDTree(nodes_xy[region_nodes])
        pairs = tree.query_pairs(max_gap)
        for a, b in pairs:
            i, j = int(region_nodes[a]), int(region_nodes[b])
            gap = float(np.hypot(*(nodes_xy[i] - nodes_xy[j])))

            if labels[i] == labels[j]:
                threshold = max(detour_factor * gap, gap + detour_add)
                net_dist = float(dijkstra(G, directed=False, indices=i, limit=threshold)[j])
                if not (net_dist > threshold):
                    continue

            x1, y1 = nodes_xy[i]
            x2, y2 = nodes_xy[j]
            rows.append({"region": region, "gap_m": gap, "x1": x1, "y1": y1, "x2": x2, "y2": y2})

    cols = ["cand_id", "region", "gap_m", "x1", "y1", "x2", "y2"]
    if not rows:
        return pd.DataFrame(columns=cols)

    df = (
        pd.DataFrame(rows)
        .sort_values(["region", "gap_m", "x1", "y1", "x2", "y2"], kind="mergesort")
        .reset_index(drop=True)
    )
    df.insert(0, "cand_id", df.index)
    return df[cols]


def link_benefit(d0, du, dv, L, d0_u, d0_v, origin_nodes, cutoff):
    """링크 (u, v, 길이 L) 추가로 새로 도달하게 되는 origin의 boolean mask.

    d0: 링크 추가 전 baseline 노드->최근접 목적지 거리 배열 (limit=cutoff로
        계산된 dest_node_distances 결과).
    du, dv: 원본 그래프(링크 추가 전)에서 u, v로부터의 거리 배열
        (limit=cutoff dijkstra) -- 링크 추가 후 그래프를 다시 만들지 않기
        위한 핵심: Step 5 공식이 이 두 배열만으로 d1을 근사한다.
    d0_u, d0_v: d0[u], d0[v] (끝점 자신의 baseline 거리).
    origin_nodes: origin별 스냅 노드 인덱스(nearest_nodes 결과). -1(스냅
        실패, snap_max 밖)은 항상 False.
    cutoff: 도달 판정 문턱값, 포함(<=).

    "한계(marginal)" 편익이므로 이미 d0로 도달하던 origin은 표시하지 않는다.
    """
    d0 = np.asarray(d0, dtype=float)
    du = np.asarray(du, dtype=float)
    dv = np.asarray(dv, dtype=float)
    origin_nodes = np.asarray(origin_nodes)

    d1 = np.minimum(d0, np.minimum(du + L + d0_v, dv + L + d0_u))

    mask = np.zeros(len(origin_nodes), dtype=bool)
    matched = origin_nodes >= 0
    nodes = origin_nodes[matched]
    already = d0[nodes] <= cutoff
    newly = (d1[nodes] <= cutoff) & ~already
    mask[matched] = newly
    return mask


def bottom_share_mask(d0_origins, pop, share=0.2, tiebreak_desc=None, stable_id=None):
    """baseline 거리(d0_origins)가 가장 먼 origin부터 인구를 누적해 전체
    인구의 share(기본 0.2)에 도달할 때까지(도달시키는 origin 포함) 표시하는
    boolean mask. inf 거리는 항상 가장 먼 것으로 취급된다 (가장 먼저 포함).
    총 인구가 0이면 전부 False.

    정렬 키(final-review C1): d0_origins 내림차순(1순위) -> tiebreak_desc
    내림차순(2순위, 예: 목적지까지 유클리드 거리) -> stable_id 오름차순
    (3순위, 행 순서와 무관한 고정 키 -- 지정하지 않으면 배열 위치를 쓴다,
    이전 버전과 동일한 동작). tiebreak_desc/stable_id를 생략하면 이전
    버전(d0만으로 stable sort)과 완전히 같은 결과를 낸다.
    """
    d0_origins = np.asarray(d0_origins, dtype=float)
    pop = np.asarray(pop, dtype=float)
    n = len(pop)
    mask = np.zeros(n, dtype=bool)

    total = pop.sum()
    if total <= 0:
        return mask

    if stable_id is None:
        stable_id = np.arange(n)
    else:
        stable_id = np.asarray(stable_id)
    if tiebreak_desc is None:
        tiebreak_desc = np.zeros(n)
    else:
        tiebreak_desc = np.asarray(tiebreak_desc, dtype=float)

    target = share * total
    # np.lexsort의 마지막 키가 1순위 -- d0 내림차순을 마지막에 둔다.
    order = np.lexsort((stable_id, -tiebreak_desc, -d0_origins))
    cum_before = np.cumsum(pop[order]) - pop[order]
    keep = cum_before < target
    mask[order[keep]] = True
    return mask


def stable_origin_rank(xy) -> np.ndarray:
    """origin의 (x, y) 좌표만으로 만든 정수 순위 -- DataFrame 행 순서를
    셔플해도 같은 좌표는 항상 같은 순위를 받는다(final-review C1의
    "map back by stable id" 요구). 좌표가 완전히 같은 origin이 둘 이상
    있으면 그 안에서는 배열 위치로 동률을 깬다(실제 인구 격자에서는
    좌표가 유일하다고 가정 -- 완전한 순열 불변성은 아니지만 실용적으로
    충분하다)."""
    xy = np.asarray(xy, dtype=float)
    order = np.lexsort((xy[:, 1], xy[:, 0]))
    rank = np.empty(len(xy), dtype=np.int64)
    rank[order] = np.arange(len(xy))
    return rank


def distinct_reach_pop(cand_ids, masks_by_cand, pop, region_mask, extra_mask=None) -> float:
    """cand_ids(후보 id 목록)의 신규도달 mask들을 OR로 합친(distinct 인구,
    한 origin은 몇 개의 링크가 닿든 한 번만 센다) 뒤 region_mask(그리고
    있으면 extra_mask)로 걸러 인구를 합산한다 (final-review C2 -- top-N
    집합의 "편익"은 링크별 한계편익의 합이 아니라 합집합 인구여야 한다).

    masks_by_cand: {cand_id: boolean ndarray 또는 None(이 config에서 후보
    사용 불가)}. None은 무시한다.
    """
    pop = np.asarray(pop, dtype=float)
    region_mask = np.asarray(region_mask, dtype=bool)
    combined = np.zeros(len(pop), dtype=bool)
    for c in cand_ids:
        m = masks_by_cand.get(c)
        if m is not None:
            combined |= m
    combined &= region_mask
    if extra_mask is not None:
        combined &= np.asarray(extra_mask, dtype=bool)
    return float(pop[combined].sum())


def tie_inclusive_topn(cand_ids, score_of, n=20, ascending=False, gap_of=None):
    """cand_ids 중 상위 n번째 점수와 동점인 후보까지 전부 포함하는 top-n
    집합을 만든다(final-review I5 -- `sorted(...)[:n]`의 암묵적
    cand_id/gap tie-break로 top-n 경계의 동점 후보가 임의로 잘려나가는
    것을 막는다).

    score_of: cand_id -> float. ascending=True면 값이 작을수록 상위(rule
    `short`의 gap_m). len(cand_ids) <= n이면 전부 포함(동률 여부 무관).
    표시 순서: gap_of가 주어지면 gap 오름차순 -> cand_id 오름차순, 아니면
    cand_id 오름차순.

    반환: (표시 순서로 정렬된 리스트, 그 리스트의 길이).
    """
    ids = list(cand_ids)
    if not ids:
        return [], 0

    ordered = sorted(ids, key=lambda c: score_of(c), reverse=not ascending)
    if len(ordered) <= n:
        threshold = score_of(ordered[-1])
    else:
        threshold = score_of(ordered[n - 1])

    if ascending:
        selected = [c for c in ids if score_of(c) <= threshold]
    else:
        selected = [c for c in ids if score_of(c) >= threshold]

    if gap_of is not None:
        selected.sort(key=lambda c: (gap_of(c), c))
    else:
        selected.sort()
    return selected, len(selected)
