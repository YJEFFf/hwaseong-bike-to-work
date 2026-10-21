"""S2 끊긴 지점 목록 + S3 보도 겸용 비율 (두 지도 합집합망, 구별).

S2: 망 끝점(연결 차수 1)이 다른 연결 덩어리의 노선과 5·10·20·50m 안에 있는데도 이어지지 않은 곳(bridge_links),
    연결 덩어리 수와 크기(1km 미만 '섬').
S3: OSM 자전거 경로의 보행자 겸용 태그(foot=designated, segregated≠yes) 연장 비율.
    공식 GIS 노선의 byc_cde는 코드 정의를 확인하지 못해 원래 코드값별 연장만 적는다.
실행: 제출레포 루트에서 `python -X utf8 <이 파일>`. 저장소 코드 수정 없음. 출력은 집계와 지점 목록(좌표 포함, 공개 자료만 사용).
"""
import csv, importlib.util, json, os
from pathlib import Path
import numpy as np
from scipy.sparse.csgraph import connected_components
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

ROOT = Path.cwd(); WORK = ROOT/'outputs/runs/loo_work'
HERE = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get('COMMUTE_OUT', HERE/'results')); OUT.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location('auto10', ROOT/'scripts/10_auto_scenarios.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
from commute.audit_support import shared_path, gap_record

from pyproj import Transformer
from shapely.ops import transform as _tf, unary_union
_to5186 = Transformer.from_crs(4326, 5186, always_xy=True).transform  # 행정동 경계는 위경도 → 망 좌표(EPSG:5186)로
fc = json.loads((WORK/'data/raw/admin/hwaseong_hjd_20260701.geojson').read_text(encoding='utf-8'))
polys, names = [], []
for f in fc['features']:
    for k in ('동탄', '병점', '효행', '만세'):
        if k+'구' in f['properties'].get('sggnm', ''):
            polys.append(_tf(_to5186, shape(f['geometry']))); names.append(k)
tree = STRtree(polys)
area_polys = {k: unary_union([p for p, name in zip(polys, names) if name == k]) for k in sorted(set(names))}
area_polys['화성시'] = unary_union(polys)


def district_of(x, y):
    p = Point(x, y)
    for i in tree.query(p):
        if polys[i].covers(p):
            return names[i]
    return '시 경계 밖'


origins, dests = m.prepare_population(WORK)
feats = m.load_features(WORK, 'union')
net = m.build_access_network(feats, origins[['x', 'y']].to_numpy(float), dests[['x', 'y']].to_numpy(float), snap=1., access_max=150.)

# 연결 덩어리
weak = net.physical_graph.maximum(net.physical_graph.T)
ncomp, comp = connected_components(weak, directed=False)
edge_len = {}
coo = weak.tocoo()
for u, v, w in zip(coo.row, coo.col, coo.data):
    if u < v:
        edge_len[comp[u]] = edge_len.get(comp[u], 0.) + w
lens = np.array(sorted(edge_len.values(), reverse=True))
summary = [dict(항목='연결 덩어리 수', 값=len(lens)),
           dict(항목='전체 연장(km)', 값=round(lens.sum()/1000, 1)),
           dict(항목='가장 큰 덩어리 비율', 값=round(lens[0]/lens.sum(), 4)),
           dict(항목='1km 미만 덩어리 수', 값=int((lens < 1000).sum())),
           dict(항목='1km 미만 덩어리 연장 비율', 값=round(lens[lens < 1000].sum()/lens.sum(), 4))]

# 끊긴 지점
gap_rows = []
fgeom = [f.geometry for f in feats]; ftree = STRtree(fgeom)
def source_of(node):  # 스냅 오차 안에서 복수 출처가 만나면 한 출처로 단정하지 않는다
    p = Point(*net.nodes[node])
    hits = [i for i in ftree.query(p.buffer(1.0)) if fgeom[i].distance(p) <= 1.0 and feats[i].level == net.levels[node]]
    sources = {'공식' if feats[i].source == 'official' else 'OSM' for i in hits}
    return next(iter(sources)) if len(sources) == 1 else '혼재' if sources else '미확인'
for u, v, length in net.bridge_links(50.):  # 50m 목록 하나에서 거리 구간별로 센다(S5와 같은 목록)
    x, y = (net.nodes[u] + net.nodes[v])/2
    sources = (source_of(u), source_of(v))
    pair = '출처혼재' if '혼재' in sources else '미확인' if '미확인' in sources else '-'.join(sorted(sources))
    gap_rows.append(dict(**gap_record(net, u, v, length), district=district_of(x, y), pair=pair, x=round(x, 1), y=round(y, 1)))
for tol in (5., 10., 20., 50.):
    sel = [r for r in gap_rows if r['gap_m_exact'] <= tol]
    summary.append(dict(항목=f'끊긴 지점(끝점 틈 ≤{tol:.0f}m)', 값=len(sel)))
    for d in ('동탄', '병점', '효행', '만세', '시 경계 밖'):
        area = d if d == '시 경계 밖' else d+'구'
        summary.append(dict(항목=f'  {area} ≤{tol:.0f}m', 값=sum(r['district'] == d for r in sel)))
    for pr in ('OSM-공식', 'OSM-OSM', '공식-공식', '출처혼재', '미확인'):
        summary.append(dict(항목=f'  {pr} ≤{tol:.0f}m', 값=sum(r['pair'] == pr for r in sel)))

# 보도 겸용 비율
share = {}
for f in feats:
    if f.source == 'official':
        key = ('공식', f"byc_cde={(f.tags or {}).get('byc_cde')}")  # 코드 뜻 미확인: 뜻을 붙이지 않고 원래 값으로 집계
    else:
        t = f.tags or {}
        shared = shared_path(t)
        key = ('OSM', '겸용' if shared else '전용·분리·기타')
    for area, polygon in area_polys.items():
        L = f.geometry.intersection(polygon).length
        share[(area,)+key] = share.get((area,)+key, 0.) + L
with open(OUT/'gaps_summary.csv', 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, ['항목', '값']); w.writeheader(); w.writerows(summary)
with open(OUT/'gap_points_50m.csv', 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, list(gap_rows[0])); w.writeheader(); w.writerows(sorted(gap_rows, key=lambda r: (r['gap_m_exact'], r['gap_id'])))
with open(OUT/'shared_path_share.csv', 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f); w.writerow(['area', 'source', 'type', 'km'])
    for k in sorted(share):
        w.writerow([*k, round(share[k]/1000, 3)])
for r in summary:
    print(r['항목'], r['값'])
for k in sorted(share):
    print(k, round(share[k]/1000, 1), 'km')
