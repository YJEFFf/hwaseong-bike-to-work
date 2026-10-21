"""공식 자전거 노선 위상망 유틸리티.

끊긴 끝점(dangling endpoints), 끊긴 지점 사이 급변 거리(knee distance),
평행 후보 쌍, 중복 라인 제거, 허용오차별 연결 성분 계산을 제공한다.
"""
import math
import collections

import numpy as np
from shapely import STRtree
from shapely.geometry import Point


def dangling_endpoints_with_source(lines, tol=0.5):
    """dangling_endpoints와 동일한 판정 로직으로, (끝점, 소속 라인 인덱스) 쌍을 반환.

    호출부가 "그 끝점이 어느 라인에서 나왔는지" 알아야 할 때(예: 자기 자신을
    제외한 최근접 타 라인까지의 거리 계산) dangling_endpoints의 판정 로직을
    중복 구현하지 않고 이 함수를 통해 재사용한다.
    """
    tree = STRtree(lines)
    out = []
    for i, g in enumerate(lines):
        for p in (Point(g.coords[0]), Point(g.coords[-1])):
            hits = [j for j in tree.query(p, predicate="dwithin", distance=tol) if j != i]
            if not hits:
                out.append((p, i))
    return out


def dangling_endpoints(lines, tol=0.5):
    """다른 라인의 끝점과 tol 이내에서 만나지 않는 끝점 목록."""
    return [p for p, _ in dangling_endpoints_with_source(lines, tol)]


def knee_distance(dists, grid):
    """dists(거리 배열)를 grid 문턱값들에 누적 카운트하고, 기울기가 가장 급변하는 지점을 반환."""
    dists = np.asarray(dists, dtype=float)
    counts = np.array([(dists <= t).sum() for t in grid], dtype=float)
    slope = np.diff(counts) / np.diff(grid)
    return float(grid[int(np.argmax(slope)) + 1])


def _bearing(g):
    (x0, y0), (x1, y1) = g.coords[0], g.coords[-1]
    return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180


def parallel_pairs(lines, dmin=8.0, dmax=40.0, angle=20.0):
    """서로 dmin~dmax m 떨어져 있고 각도 차가 angle 이내인 라인 쌍(i<j)."""
    tree = STRtree(lines)
    out = []
    for i, g in enumerate(lines):
        for j in tree.query(g, predicate="dwithin", distance=dmax):
            j = int(j)
            if j <= i:
                continue
            d = g.distance(lines[j])
            da = abs(_bearing(g) - _bearing(lines[j]))
            da = min(da, 180 - da)
            if d >= dmin and da <= angle:
                out.append((i, j))
    return out


def dedupe(lines, hausdorff=2.0):
    """hausdorff 거리가 hausdorff 미만인 라인을 중복으로 보고, 남길 인덱스만 반환."""
    keep = []
    for i, g in enumerate(lines):
        if not any(g.hausdorff_distance(lines[k]) < hausdorff for k in keep):
            keep.append(i)
    return keep


def components(lines, extra_edges, tol):
    """tol 이내로 서로 닿는(+ extra_edges로 명시적으로 이어진) 라인들을 하나의 성분으로 묶어
    성분별 총 길이를 내림차순으로 반환."""
    tree = STRtree(lines)
    par = list(range(len(lines)))

    def f(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    a, b = tree.query(lines, predicate="dwithin", distance=tol)
    for i, j in list(zip(a, b)) + list(extra_edges):
        if i != j:
            par[f(int(i))] = f(int(j))

    agg = collections.defaultdict(float)
    for i, g in enumerate(lines):
        agg[f(i)] += g.length
    return sorted(agg.values(), reverse=True)
