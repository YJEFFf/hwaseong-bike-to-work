"""공식(화성시) 자전거 GIS와 OSM 자전거 시설의 출처 합의 레이어.

두 출처를 20m(기본) 간격의 짧은 조각으로 쪼갠 뒤, 조각 단위로 서로 근접
(`buf_line`/`buf_poly`)한지 판정해 세 층(stratum)으로 나눈다:

- "합의"      : 공식 조각이 OSM 라인 `buf_line` 이내에 있음
- "공식단일"  : 공식 조각인데 OSM 라인이 근처에 없음
- "OSM단일"   : OSM 조각인데 공식 선형/면형 어느 쪽 근처에도 없음

합의는 공식 쪽에서만 한 번 센다 -- OSM 조각이 공식 선형(`buf_line`) 또는
공식 면형(`buf_poly`)에 근접하면 그 조각은 반환되는 프레임에서 아예
제외한다(이미 공식 쪽 "합의" 행으로 집계됐기 때문에 이중 계산을 막는다).
그 결과 반환되는 프레임에 남는 OSM 조각은 정의상 전부 공식 버퍼 밖에
있는 부분이므로, `in_osm` 컬럼은 (자기 자신까지 포함해 거리 0으로 항상
`dwithin`을 만족하는) OSM-origin 행에서는 항상 `True`다 -- 의미 있는
신호가 아니라 컬럼 정의상의 자명한 결과이니 유의할 것.

district(구) 배정은 화성시 자전거 GIS가 아니라 별도의 행정구역 경계
(`admin_polys`/`admin_labels`, 미리 dissolve된 4개 구 폴리곤과 그 이름)를
받아, 각 조각의 중점(`shapely.line_interpolate_point(seg, 0.5,
normalized=True)`)이 어느 구 폴리곤에 포함되는지로 정한다. 포함되는 구가
없으면 `outside_threshold`(기본 200m) 이내의 가장 가까운 구에 배정하고,
그마저도 없으면 "화성외"로 분류한다. `admin_polys`가 없으면(기본값) 이전과
동일하게 district는 전부 `None`이다.
"""
import numpy as np
import pandas as pd
from shapely import STRtree, line_interpolate_point
from shapely.ops import substring

DISTRICT_MAP = {"41597": "동탄", "41595": "병점", "41593": "효행", "41591": "만세"}
OUTSIDE_DISTRICT = "화성외"

COLUMNS = [
    "seg_id", "origin", "district", "in_official_line", "in_official_poly",
    "in_osm", "stratum", "length_m", "geometry",
]


def split_line(line, step=20.0):
    """`line`을 `step` 간격의 조각으로 나눈다. 마지막 조각은 더 짧을 수 있고,
    길이 0인 조각은 결코 반환하지 않는다."""
    length = line.length
    if length <= 0:
        return []

    cuts = list(np.arange(0.0, length, step))
    cuts.append(length)

    parts = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        if b - a < 1e-9:
            continue
        seg = substring(line, a, b)
        if seg.length > 0:
            parts.append(seg)
    return parts


def _dwithin_any(tree, geom, buf):
    if tree is None:
        return False
    return len(tree.query(geom, predicate="dwithin", distance=buf)) > 0


def district_for_point(point, admin_tree, admin_polys, admin_labels, outside_threshold=200.0):
    """`point`가 포함되는 구, 아니면 `outside_threshold` 이내의 최근접 구,
    그마저도 없으면 "화성외"를 반환한다. `admin_tree`가 없으면
    (구 폴리곤이 주어지지 않았으면) `None`.

    `_district_for`(세그먼트 중점 기준)의 얇은 점 버전 -- 세그먼트가 아니라
    이미 만들어진 점(예: Task 5b 표본추출의 후보지 중점, 섬 갭 중점,
    플레이스홀더 대표점)에 구를 배정할 때 이 함수를 직접 쓴다."""
    if admin_tree is None:
        return None

    contains_hits = admin_tree.query(point, predicate="intersects")
    if len(contains_hits):
        return admin_labels[int(contains_hits[0])]

    nearest_idx = int(admin_tree.nearest(point))
    if point.distance(admin_polys[nearest_idx]) <= outside_threshold:
        return admin_labels[nearest_idx]
    return OUTSIDE_DISTRICT


def _district_for(admin_tree, admin_polys, admin_labels, seg, outside_threshold=200.0):
    """조각 `seg`의 중점에 대해 `district_for_point`를 적용."""
    if admin_tree is None:
        return None
    mid = line_interpolate_point(seg, 0.5, normalized=True)
    return district_for_point(mid, admin_tree, admin_polys, admin_labels, outside_threshold)


def label_segments(official_lines, official_polys, osm_lines,
                    buf_line=10.0, buf_poly=3.0,
                    admin_polys=None, admin_labels=None, outside_threshold=200.0,
                    step=20.0):
    official_lines = list(official_lines)
    official_polys = list(official_polys)
    osm_lines = list(osm_lines)
    admin_polys = list(admin_polys) if admin_polys is not None else None
    admin_labels = list(admin_labels) if admin_labels is not None else None

    official_line_tree = STRtree(official_lines) if official_lines else None
    official_poly_tree = STRtree(official_polys) if official_polys else None
    osm_line_tree = STRtree(osm_lines) if osm_lines else None
    admin_tree = STRtree(admin_polys) if admin_polys else None

    rows = []
    seg_id = 0

    for line in official_lines:
        for seg in split_line(line, step):
            in_official_line = _dwithin_any(official_line_tree, seg, buf_line)
            in_official_poly = _dwithin_any(official_poly_tree, seg, buf_poly)
            in_osm = _dwithin_any(osm_line_tree, seg, buf_line)
            stratum = "합의" if in_osm else "공식단일"
            rows.append({
                "seg_id": seg_id, "origin": "official",
                "district": _district_for(admin_tree, admin_polys, admin_labels, seg,
                                           outside_threshold),
                "in_official_line": in_official_line, "in_official_poly": in_official_poly,
                "in_osm": in_osm, "stratum": stratum, "length_m": seg.length, "geometry": seg,
            })
            seg_id += 1

    for line in osm_lines:
        for seg in split_line(line, step):
            in_official_line = _dwithin_any(official_line_tree, seg, buf_line)
            in_official_poly = _dwithin_any(official_poly_tree, seg, buf_poly)
            if in_official_line or in_official_poly:
                continue  # 합의 구간은 공식 쪽에서만 센다 -- 이중 계산 방지
            in_osm = _dwithin_any(osm_line_tree, seg, buf_line)
            rows.append({
                "seg_id": seg_id, "origin": "osm",
                "district": _district_for(admin_tree, admin_polys, admin_labels, seg,
                                           outside_threshold),
                "in_official_line": in_official_line, "in_official_poly": in_official_poly,
                "in_osm": in_osm, "stratum": "OSM단일", "length_m": seg.length, "geometry": seg,
            })
            seg_id += 1

    return pd.DataFrame(rows, columns=COLUMNS)
