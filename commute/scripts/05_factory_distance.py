"""공장 일자리가 자전거 시설(두 지도 합집합)에서 얼마나 떨어져 있나 — 구별 150·300·500m 안 종업원 비율.
출력은 구별 집계만. 실행: 제출레포 루트에서 `python -X utf8 <이 파일>`."""
import csv, importlib.util, json
from pathlib import Path
import numpy as np
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform as _tf
from shapely.strtree import STRtree

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('ca', HERE/'02_commute_access.py')
ca = importlib.util.module_from_spec(spec); spec.loader.exec_module(ca)
to5186 = Transformer.from_crs(4326, 5186, always_xy=True).transform
fc = json.loads((ca.WORK/'data/raw/admin/hwaseong_hjd_20260701.geojson').read_text(encoding='utf-8'))
polys, names = [], []
for f in fc['features']:
    for k in ('동탄', '병점', '효행', '만세'):
        if k+'구' in f['properties'].get('sggnm', ''):
            polys.append(_tf(to5186, shape(f['geometry']))); names.append(k)
ptree = STRtree(polys)
fxy, femp, fdan, miss = ca.load_factories()
lines = [f.geometry for f in ca.m.load_features(ca.WORK, 'union')]
ltree = STRtree(lines)
rows = {}
for (x, y), e in zip(fxy, femp):
    p = Point(x, y)
    d = min((lines[i].distance(p) for i in ltree.query(p.buffer(500))), default=np.inf)
    dist = next((names[i] for i in ptree.query(p) if polys[i].contains(p)), '기타')
    for area in ('화성시', dist):
        r = rows.setdefault(area, dict(area=area, jobs=0., w150=0., w300=0., w500=0.))
        r['jobs'] += e
        for c in (150, 300, 500):
            r[f'w{c}'] += e*(d <= c)
out = []
for area in ('화성시', '동탄', '병점', '효행', '만세'):
    r = rows[area]
    out.append(dict(area=area, jobs=round(r['jobs']), within150=round(r['w150']/r['jobs'], 4),
                    within300=round(r['w300']/r['jobs'], 4), within500=round(r['w500']/r['jobs'], 4)))
    print(out[-1])
with open(ca.OUT/'factory_distance_to_bike.csv', 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, out[0].keys()); w.writeheader(); w.writerows(out)
