"""Critic 2라운드 확인: ① 공장 진입 구간(노선에서 150~300m 공장 지점)의 거리 규모 ② 최대 종업원 집계점 제외 민감도
③ 근사 위치(수동 보정·산단/리동 평균) 공장을 뺀 민감도 ④ 탐욕 상위 20곳 중 두 지도가 어긋난 곳을 뺀 묶음 효과.
기준: 근로 연령 인구, 망 거리 3.75km(15분), 접속 150m. 실행: 제출레포 루트에서. 출력은 집계만."""
import csv, importlib.util
from pathlib import Path
import numpy as np
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree
from shapely.geometry import Point
from shapely.strtree import STRtree

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('ca', HERE/'02_commute_access.py'); ca = importlib.util.module_from_spec(spec); spec.loader.exec_module(ca)
spec = importlib.util.spec_from_file_location('gp', HERE/'04_gap_priority_commute.py'); gp = importlib.util.module_from_spec(spec); spec.loader.exec_module(gp)
m, WORK, OUT, CUT = ca.m, ca.WORK, ca.OUT, 3750.
from commute.audit_support import map_gap_records
origins, dests = m.prepare_population(WORK)
oxy = origins[['x', 'y']].to_numpy(float); n = len(oxy); wpop = ca.working_age_weights(origins)
fxy, femp, fdan, miss, fapprox = ca.load_factories(flags=True)
feats = m.load_features(WORK, 'union')
nd = len(dests)
res = []

# ① 진입 구간 거리
geoms = [f.geometry for f in feats if f.lateral_access]; tree = STRtree(geoms)
dists = np.array([min((geoms[j].distance(Point(x, y)) for j in tree.query(Point(x, y).buffer(300))), default=np.inf) for x, y in fxy])
sel = (dists > 150) & (dists <= 300)
res += [dict(항목='진입 구간 대상 공장 지점', 값=int(sel.sum())), dict(항목='  종업원', 값=round(femp[sel].sum())),
        dict(항목='  노선까지 거리 합(km)', 값=round(dists[sel].sum()/1000, 1)), dict(항목='  150m를 넘는 부분 합(km)', 값=round((dists[sel]-150).sum()/1000, 1))]


def share(fx, fe, keep, tol=0., factory_access=False):
    """직선 15분 안 일자리 중 자전거망으로 닿는 몫(근로 연령 가중). keep=False인 공장 점은 분자·분모에서 뺀다. tol>0이면 끝점 틈 연결."""
    dxy = np.vstack([dests[['x', 'y']].to_numpy(float), fx])
    net = ca.factory_access_network(feats, oxy, dxy, nd) if factory_access else m.build_access_network(feats, oxy, dxy, snap=1., access_max=150.)
    g = net.with_links(ca.fixed_gap_links(net, tol)) if tol else net.graph
    rev = g.T.tocsr(); fn = net.destination_nodes[nd:]; on = net.origin_nodes[:n]
    att = (np.diff(rev.indptr)[fn] > 0) & keep
    reach = np.zeros(n)
    for node, e in zip(fn[att], fe[att]):
        reach += e*(dijkstra(rev, directed=True, indices=int(node), limit=CUT)[on] <= CUT)
    kt = cKDTree(fx[keep]); ek = fe[keep]; straight = np.zeros(n)
    for i0 in range(0, n, 2000):
        for j, idx in enumerate(kt.query_ball_point(oxy[i0:i0+2000], CUT)):
            straight[i0+j] = ek[idx].sum()
    return float((wpop*reach).sum()/(wpop*straight).sum())


largest = np.argmax(femp)
for name, keep in (('전체', np.ones(len(fxy), bool)), ('최대 종업원 집계점 제외', np.arange(len(fxy)) != largest), ('근사 위치 공장 제외', ~fapprox)):
    v = [share(fxy, femp, keep), share(fxy, femp, keep, 20.), share(fxy, femp, keep, factory_access=True), share(fxy, femp, keep, 20., factory_access=True)]
    res.append(dict(항목=f'닿는 몫 현재/틈20m/진입구간/둘다 — {name}', 값=' / '.join(f'{x*100:.1f}%' for x in v)))
    print(res[-1], flush=True)
res.append(dict(항목='최대 종업원 집계점 종업원', 값=round(femp[largest])))
res.append(dict(항목='근사 위치 공장 점·종업원', 값=f'{int(fapprox.sum())}점, {femp[fapprox].sum():.0f}명'))

# ④ 탐욕 상위 20곳 중 두 지도 어긋남 제외(개수는 재계산한 목록에서 결정)
net = m.build_access_network(feats, oxy, np.vstack([dests[['x', 'y']].to_numpy(float), fxy]), snap=1., access_max=150.)
major = (femp >= 50) | fdan; mask = np.r_[np.zeros(nd, bool), major]; on = net.origin_nodes[:n]
base = net.distances(mask, limit=CUT)[on] <= CUT
greedy = list(csv.DictReader(open(OUT/'gap_priority_greedy.csv', encoding='utf-8')))
chosen = list(zip(map_gap_records(net, greedy[:20]), [r['pair'] for r in greedy[:20]]))
for name, ls in (('상위 20곳', [l for l, _ in chosen]), ('상위 20곳 중 두 지도 어긋남 제외', [l for l, p in chosen if p != 'OSM-공식'])):
    dd = net.distances(mask, limit=CUT, graph=net.with_links(ls))[on] <= CUT
    res.append(dict(항목=f'{name}({len(ls)}곳) 새로 주요 일터 닿는 근로 연령', 값=round(float(wpop[dd & ~base].sum()))))
    print(res[-1], flush=True)
with open(OUT/'sensitivity_round2.csv', 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, ['항목', '값']); w.writeheader(); w.writerows(res)
for r in res:
    print(r['항목'], r['값'])
