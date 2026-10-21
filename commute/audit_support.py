"""출퇴근 분석의 거리·분류·후보 식별 보조 함수. 기존 analysis 코드는 수정하지 않는다."""
import hashlib
import re

import numpy as np
from analysis.routing import build_access_network


def locality_key(address):
    """읍·면이 다른 동명 리를 같은 좌표 보완 그룹으로 묶지 않는다."""
    match = re.search(r'화성시\s+(?:(\S+[읍면])\s+)?(\S+?[리동가])(?=\s|산|\d)', address)
    return ' '.join(p for p in match.groups() if p) if match else ''


def shared_path(tags):
    """분리 표기와 보행 금지 표기는 highway의 암묵적 보행 허용보다 우선한다."""
    return (tags.get('segregated') != 'yes'
            and tags.get('foot') not in ('no', 'private')
            and (tags.get('foot') in ('designated', 'yes')
                 or tags.get('highway') in ('footway', 'path', 'pedestrian')))


def factory_access_network(features, origins, destinations, factory_start, *,
                           origin_max=150., factory_max=300., base_max=150.):
    """좌표를 유지하고 공장 목적지의 접속 반경만 늘린다.

    원래 위치부터 노선까지 실제 직선 접속 비용을 보존한다. 공장 이외 목적지와
    출발지에는 각각 기존 반경을 적용한다. 기존 반경 밖의 공장만 300m 안 적격 노선에 접속하므로
    점 이동의 경로 누락/거리 축소를 피한다. 기존 엔진의 1m 스냅 가정은 유지한다.
    """
    net = build_access_network(features, origins, destinations, snap=1.,
                               access_max=max(origin_max, factory_max, base_max))
    nphys = len(net.nodes)
    import shapely
    active = [f.geometry for f in features if f.lateral_access]
    factories = np.asarray(destinations)[factory_start:]
    factory_limits = np.full(len(factories), base_max)
    if active and len(factories):
        geoms = np.asarray(active, dtype=object)
        points = shapely.points(factories)
        nearest = shapely.STRtree(geoms).nearest(points)
        distance = shapely.distance(points, geoms[nearest])
        eligible = (distance > base_max) & (distance <= factory_max)
        factory_limits[eligible] = factory_max
    limits = np.r_[np.full(len(origins), origin_max),
                   np.full(factory_start, base_max), factory_limits]
    coo = net.graph.tocoo()
    aux = np.maximum(coo.row, coo.col) - nphys
    connectors = aux >= 0
    retain = np.ones(len(coo.data), dtype=bool)
    retain[connectors] = coo.data[connectors] <= limits[aux[connectors]] + 1e-9
    from scipy.sparse import coo_matrix
    net.graph = coo_matrix((coo.data[retain], (coo.row[retain], coo.col[retain])), shape=coo.shape).tocsr()
    net.detached = np.diff(net.graph.indptr)[net.origin_nodes] == 0
    net.connector_count = int(np.sum(retain & connectors))
    return net


def gap_record(net, a, b, length):
    points = sorted([(str(net.levels[a]), *map(float, net.nodes[a])),
                     (str(net.levels[b]), *map(float, net.nodes[b]))])
    text = '|'.join(f'{level}:{x:.6f}:{y:.6f}' for level, x, y in points)
    return dict(gap_id=hashlib.sha256(text.encode()).hexdigest()[:16],
                gap_m=round(length, 1), gap_m_exact=float(length),
                ax=points[0][1], ay=points[0][2], level_a=points[0][0],
                bx=points[1][1], by=points[1][2], level_b=points[1][0])


def map_gap_records(net, rows):
    """중점 대신 계층과 양 끝점으로 특정하고, 대응 불가·중복이면 중단한다."""
    keys = [(str(level), float(x), float(y)) for level, (x,y) in zip(net.levels, net.nodes)]
    nodes = {k: i for i,k in enumerate(keys)}
    if len(nodes) != len(keys):
        raise ValueError('좌표와 계층이 같은 물리 노드가 중복됨')
    links, seen = [], set()
    for row in rows:
        if 'gap_id' not in row or 'gap_m_exact' not in row:
            raise ValueError('03_gaps_and_shared.py를 먼저 재실행해 양 끝점과 gap_id를 생성하세요')
        if row['gap_id'] in seen:
            raise ValueError('중복 gap_id')
        seen.add(row['gap_id'])
        endpoints = [(str(row[f'level_{side}']), float(row[f'{side}x']), float(row[f'{side}y'])) for side in ('a','b')]
        if any(k not in nodes for k in endpoints):
            raise ValueError(f"끝점/계층 대응 실패: {row['gap_id']}")
        links.append((nodes[endpoints[0]], nodes[endpoints[1]], float(row['gap_m_exact'])))
    return links


def weighted_distance_stats(distances, population, mask):
    d, w = np.asarray(distances)[mask], np.asarray(population)[mask]
    positive = w > 0
    d, w = d[positive], w[positive]
    if not len(d):
        return 0., 0., 0.
    if not np.isfinite(d).all():
        raise ValueError('도달한 주민의 거리에는 무한값을 넣을 수 없습니다')
    order = np.argsort(d, kind='stable')
    median = d[order][np.searchsorted(np.cumsum(w[order]), w.sum()/2, side='left')]
    # 평균 거리 × 주민 수는 격자별 주민 수 × 거리의 합과 같다.
    return float(median), float(np.average(d, weights=w)), float(np.dot(d, w))
