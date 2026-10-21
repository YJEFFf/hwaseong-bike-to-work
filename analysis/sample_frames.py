"""판독 표본(Task 5b) 추출용 순수 함수 모음.

세그먼트/후보지/섬 갭/플레이스홀더 4개 프레임을 조합해 190지점 표본을 만드는
scripts/03_sample.py 가 이 함수들을 사용한다.

- cluster_points: 30m 이내 전이적(transitive) 단일연결(single-linkage) 클러스터링
- midpoint: LineString 중점 / Polygon representative_point
- island_gaps: tol 이내로 닿는 라인을 성분으로 묶고, 총 길이 조건을 만족하는
  성분 쌍 사이의 최근접 지점 간극을 오름차순으로 반환
- cluster_representatives: 클러스터링 + 클러스터당 대표 1개 + district 배정
  (후보지 프레임 빌더). district 배정 자체는 analysis.agreement.district_for_point
  를 그대로 쓴다 -- 이 모듈이 자체 district 판정 로직을 갖지 않는다(Task 4의
  행정구역 dissolve 기반 판정과 어긋나는 사본을 만들지 않기 위함, fix round 1).
"""
import collections
import itertools

import numpy as np
import pandas as pd
import shapely
from shapely import STRtree
from shapely.geometry import Point
from shapely.ops import nearest_points, unary_union

from analysis.agreement import district_for_point


def cluster_points(xy: np.ndarray, radius: float) -> np.ndarray:
    """xy(N,2) 점들을 radius 이내 전이적 연결로 묶어 점마다 클러스터 라벨(정수)을 반환.

    A-B, B-C가 각각 radius 이내면 A-C가 radius를 넘더라도 하나의 클러스터로
    묶인다(단일연결/single-linkage). 라벨은 점의 첫 등장 순서대로 0부터 매긴다.
    """
    xy = np.asarray(xy, dtype=float)
    n = len(xy)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            d = float(np.hypot(xy[i, 0] - xy[j, 0], xy[i, 1] - xy[j, 1]))
            if d <= radius:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    labels = np.empty(n, dtype=int)
    seen = {}
    for i in range(n):
        r = find(i)
        if r not in seen:
            seen[r] = len(seen)
        labels[i] = seen[r]
    return labels


def midpoint(geom):
    """LineString은 정규화 0.5 지점(중점), Polygon/MultiPolygon은 representative_point,
    Point는 그대로 반환한다."""
    if geom.geom_type == "LineString":
        return shapely.line_interpolate_point(geom, 0.5, normalized=True)
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom.representative_point()
    if geom.geom_type == "Point":
        return geom
    raise ValueError(f"unsupported geometry type for midpoint: {geom.geom_type}")


def island_gaps(lines, tol, min_component_len, k, include_geometry=False):
    """tol 이내로 서로 닿는 라인을 하나의 성분으로 묶은 뒤, 총 길이가
    min_component_len 이상인 성분끼리의 최근접 지점 간극(gap)을 오름차순으로
    최대 k개 반환한다. k보다 적으면 있는 만큼만 반환한다.

    반환: [(간극 연결선의 중점 Point, 성분 a 대표 인덱스, 성분 b 대표 인덱스,
            간극 거리 m), ...] (거리 오름차순)
    """
    lines = list(lines)
    n = len(lines)
    if n == 0:
        return []

    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    tree = STRtree(lines)
    a_idx, b_idx = tree.query(lines, predicate="dwithin", distance=tol)
    for i, j in zip(a_idx, b_idx):
        i, j = int(i), int(j)
        if i != j:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

    members = collections.defaultdict(list)
    for i in range(n):
        members[find(i)].append(i)

    qualifying = {
        root: idxs for root, idxs in members.items()
        if sum(lines[i].length for i in idxs) >= min_component_len
    }
    roots = sorted(qualifying)
    geoms = {root: unary_union([lines[i] for i in idxs]) for root, idxs in qualifying.items()}

    gaps = []
    for ra, rb in itertools.combinations(roots, 2):
        pa, pb = nearest_points(geoms[ra], geoms[rb])
        gap_m = pa.distance(pb)
        mid = Point((pa.x + pb.x) / 2.0, (pa.y + pb.y) / 2.0)
        entry = (mid, ra, rb, gap_m)
        if include_geometry:
            entry += (shapely.LineString([pa, pb]),)
        gaps.append(entry)

    gaps.sort(key=lambda t: t[3])
    return gaps[:k]


def cluster_representatives(df, x_col, y_col, radius, rank_col,
                             admin_tree=None, admin_polys=None, admin_labels=None,
                             outside_threshold=200.0):
    """df의 (x_col,y_col) 점들을 radius 이내 단일연결(cluster_points)로 묶고,
    클러스터마다 rank_col이 가장 작은 행을 대표로 골라 새 DataFrame으로 반환한다.

    admin_tree가 주어지면 각 대표점에 대해
    analysis.agreement.district_for_point(point, admin_tree, admin_polys,
    admin_labels, outside_threshold)로 "district" 컬럼을 채운다. admin_tree가
    없으면 "district"는 전부 None.

    반환 DataFrame의 행 순서는 클러스터 라벨(첫 등장 순) 순이며, 원본 인덱스는
    보존하지 않는다.
    """
    df = df.reset_index(drop=True)
    if len(df) == 0:
        out = df.copy()
        out["district"] = pd.Series(dtype=object)
        return out

    xy = df[[x_col, y_col]].to_numpy(dtype=float)
    labels = cluster_points(xy, radius)

    work = df.copy()
    work["_cluster"] = labels
    reps = [g.loc[g[rank_col].idxmin()] for _, g in work.groupby("_cluster")]
    reps_df = pd.DataFrame(reps).drop(columns=["_cluster"]).reset_index(drop=True)

    if admin_tree is not None:
        reps_df["district"] = [
            district_for_point(Point(x, y), admin_tree, admin_polys, admin_labels,
                                outside_threshold)
            for x, y in zip(reps_df[x_col], reps_df[y_col])
        ]
    else:
        reps_df["district"] = None

    return reps_df
