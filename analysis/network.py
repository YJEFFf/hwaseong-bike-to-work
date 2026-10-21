"""통행망 그래프 구축 및 누적기회 접근성(reach) 계산.

- build_graph: LineString 목록 -> (희소 인접행렬, 노드 좌표 배열)
- nearest_nodes: 좌표 배열 -> 가장 가까운 노드 인덱스 (max_d 초과 시 -1)
- reach_weights: origin별로 cutoff 이내에 도달 가능한 dest 가중치 합
- bridge_components: 서로 다른 연결요소의 끝점(차수 1)을 tol 이내에서 이어붙임
  (Task 12c fix round 1 -- 버퍼 경계에서 잘린 조각이 snap 격자로는 합쳐지지
  않는 경우를 보정)
"""
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree


def build_graph(lines, snap=1.0):
    keys, xy = {}, []
    edges = {}  # (min(i,j), max(i,j)) -> minimum edge length seen

    def nid(p):
        k = (round(p[0] / snap), round(p[1] / snap))
        if k not in keys:
            keys[k] = len(xy)
            xy.append(p)
        return keys[k]

    for g in lines:
        cs = list(g.coords)
        for a, b in zip(cs, cs[1:]):
            i, j = nid(a[:2]), nid(b[:2])
            if i == j:
                continue  # zero-length self-loop, skip
            d = float(np.hypot(b[0] - a[0], b[1] - a[1]))
            key = (i, j) if i < j else (j, i)
            if key not in edges or d < edges[key]:
                edges[key] = d

    rows, cols, w = [], [], []
    for (i, j), d in edges.items():
        rows += [i, j]
        cols += [j, i]
        w += [d, d]
    n = len(xy)
    return coo_matrix((w, (rows, cols)), shape=(n, n)).tocsr(), np.array(xy)


def nearest_nodes(nodes_xy, pts_xy, max_d):
    nodes_xy = np.asarray(nodes_xy)[:, :2]
    pts_xy = np.atleast_2d(pts_xy)[:, :2]
    d, idx = cKDTree(nodes_xy).query(pts_xy)
    idx[d > max_d] = -1
    return idx


def bridge_components(G, nodes_xy, tol=10.0):
    """서로 다른 연결요소에 속한 끝점(차수 1인 노드)을 tol(m) 이내에서 이어붙인다.

    두 조각이 지도상으로는 tol 이내에서 만나지만(예: 버퍼 경계에서 잘린 곳,
    편집 오차) snap 격자가 정확히 맞지 않아 build_graph에서 같은 노드로
    합쳐지지 않은 경우를 보정한다 (Task 12c fix round 1 -- union 망을
    official 전체 + osm 전체로 재정의하면서 필요해진 보정).

    연결요소 라벨(scipy connected_components)은 브리지를 추가하기 전 원본
    G 기준으로 딱 한 번만 계산한다 -- 브리지를 하나씩 넣을 때마다 다시
    계산해 이미 합쳐진 성분끼리 또 잇는 것을 막으려는 목적이 아니라,
    "다른 성분"이라는 판단 기준 자체를 고정하기 위함이다 (여러 차수-1
    노드가 같은 성분 쌍을 여러 번 이어도 무방 -- reachability에는
    해롭지 않다).

    차수 1인 노드만 대상으로 한다(끊긴 세그먼트의 끝, T자/교차점은 제외).
    각 차수-1 노드에 대해 tol 이내의 후보 중 '다른' 연결요소에 속하면서
    가장 가까운 노드 하나를 골라 유클리드 거리 가중치의 무방향 엣지를
    추가한다. 이미 추가된 (min,max) 노드쌍은 건너뛴다.

    Returns: (G_bridged, n_bridges, bridge_len_m)
        G_bridged: 브리지가 없으면 원본 G 그대로, 있으면 브리지 엣지가
            추가된 새 csr 행렬(대칭, 양방향 nnz 포함).
        n_bridges: 추가된 브리지 엣지 수 (무방향 엣지 개수, 양방향 nnz의
            절반이 아니라 이 개수 자체가 "다리 개수"다).
        bridge_len_m: 브리지 엣지 길이 합(m, 무방향 엣지당 한 번씩만 합산).
    """
    nodes_xy = np.asarray(nodes_xy)[:, :2]
    n_components, labels = connected_components(G, directed=False)

    degree = np.asarray((G != 0).sum(axis=1)).ravel()
    degree1 = np.where(degree == 1)[0]

    if degree1.size == 0:
        return G, 0, 0.0

    tree = cKDTree(nodes_xy)

    added = {}  # (min(i,j), max(i,j)) -> length
    for i in degree1:
        candidates = tree.query_ball_point(nodes_xy[i], tol)
        best_j, best_d = -1, None
        for j in candidates:
            if j == i or labels[j] == labels[i]:
                continue
            d = float(np.hypot(*(nodes_xy[j] - nodes_xy[i])))
            if best_d is None or d < best_d:
                best_j, best_d = j, d
        if best_j < 0:
            continue
        key = (i, best_j) if i < best_j else (best_j, i)
        if key in added or G[key[0], key[1]] != 0:
            continue
        added[key] = best_d

    if not added:
        return G, 0, 0.0

    rows, cols, w = [], [], []
    for (i, j), d in added.items():
        rows += [i, j]
        cols += [j, i]
        w += [d, d]

    extra = coo_matrix((w, (rows, cols)), shape=G.shape)
    G_bridged = (G + extra).tocsr()
    return G_bridged, len(added), float(sum(added.values()))


def reach_weights(G, nodes_xy, origins_xy, dests_xy, dests_w, cutoff=2500.0, snap_max=150.0):
    o = nearest_nodes(nodes_xy, origins_xy, snap_max)
    t = nearest_nodes(nodes_xy, dests_xy, snap_max)
    out = np.zeros(len(origins_xy))
    ok = o >= 0
    if not ok.any():
        return out
    dist = dijkstra(G, directed=False, indices=o[ok], limit=cutoff)
    valid = t >= 0
    reach = np.zeros((ok.sum(), len(t)), bool)
    reach[:, valid] = dist[:, t[valid]] <= cutoff
    out[ok] = reach @ dests_w
    return out
