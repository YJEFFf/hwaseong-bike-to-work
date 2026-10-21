"""S5 끊긴 지점 우선순위(출퇴근) + S6 조건부 탄소.

- 끊긴 지점: 합집합망의 끝점 틈(bridge_links 50m 이내). 틈 길이를 그대로 연결 길이로 둔다(검증 전 가정).
- 지점별 점수: 그 한 곳만 이었을 때 새로 '주요 일터'(같은 주소 종업원 50명 이상 또는 산업단지 소속 공장)에
  망 거리 3.75km(시속 15km로 15분) 안에 닿게 되는 근로 연령(20~64세) 인구. 한 간선 추가의 한계효과를 계산한다.
- 묶음 효과: 상위 10·20곳, 20m 이내 전부를 한꺼번에 이었을 때 도달 인구와 평균 도달 일자리를 다시 계산.
- 탄소(감축 주장 아님): 새로 닿은 격자의 Σ(근로 연령 인구 × 편도 망 거리) × 자동차 통근 비율(62.57%)
  × 전환율 5·10·20% × 왕복 2 × 연 250일 × 배출계수(신차 평균 125.2g/km, 영국 DESNZ 168g/km).
출력: 지점 순위(망 꼭짓점 좌표, 공개 자료)와 집계만. 공장 좌표는 쓰지 않는다. 실행: 제출레포 루트에서.
"""
import csv, importlib.util, time
from pathlib import Path
import numpy as np
from scipy.sparse.csgraph import dijkstra

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('ca', HERE/'02_commute_access.py')
ca = importlib.util.module_from_spec(spec); spec.loader.exec_module(ca)
m, WORK, OUT = ca.m, ca.WORK, ca.OUT
from commute.audit_support import map_gap_records, weighted_distance_stats
CUT = 3750.
CAR_SHARE = 0.6257
EFS = {'신차 평균 125.2g/km': 0.1252, '영국 DESNZ 168g/km': 0.168}


def job_reach(net, graph, fnodes, femp, n):
    rev = graph.T.tocsr(); onodes = net.origin_nodes[:n]
    att = np.diff(rev.indptr)[fnodes] > 0
    reach = np.zeros(n)
    for node, e in zip(fnodes[att], femp[att]):
        reach += e*(dijkstra(rev, directed=True, indices=int(node), limit=CUT)[onodes] <= CUT)
    return reach


def main():
    t0 = time.time()
    origins, dests = m.prepare_population(WORK)
    oxy = origins[['x', 'y']].to_numpy(float); n = len(oxy)
    wpop = ca.working_age_weights(origins)
    fxy, femp, fdan, miss = ca.load_factories()
    major = (femp >= 50) | fdan
    print(f'공장 점 {len(fxy)}(종업원 {femp.sum():.0f}), 주요 일터 {major.sum()}곳(종업원 {femp[major].sum():.0f})', flush=True)
    net = m.build_access_network(m.load_features(WORK, 'union'), oxy, np.vstack([dests[['x', 'y']].to_numpy(float), fxy]), snap=1., access_max=150.)
    nd = len(dests); fnodes = net.destination_nodes[nd:]
    mask = np.r_[np.zeros(nd, bool), major]
    d0 = net.distances(mask, limit=CUT)
    onodes = net.origin_nodes[:n]
    before = d0[onodes]
    rev = net.graph.T.tocsr()
    # 공장 점을 추가해도 후보가 바뀌지 않도록 03의 후보 양 끝점·계층을 고정한다.
    gap_records = list(csv.DictReader(open(OUT/'gap_points_50m.csv', encoding='utf-8')))
    links = map_gap_records(net, gap_records)
    rows = []
    for i, (a, b, L) in enumerate(links):
        du = dijkstra(rev, directed=True, indices=a, limit=CUT)[onodes]
        dv = dijkstra(rev, directed=True, indices=b, limit=CUT)[onodes]
        after = np.minimum(before, np.minimum(du+L+d0[b], dv+L+d0[a]))
        gained = (before > CUT) & (after <= CUT)
        rows.append(dict(**gap_records[i], rank=0, gained_workers=round(float(wpop[gained].sum())), link=(a, b, L)))
    rows.sort(key=lambda r: (-r['gained_workers'], float(r['gap_m_exact']), r['gap_id']))
    for k, r in enumerate(rows, 1):
        r['rank'] = k
    print(f'지점 {len(links)}곳 평가 {time.time()-t0:.0f}s', flush=True)
    with open(OUT/'gap_priority_commute.csv', 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, [k for k in rows[0] if k != 'link']); w.writeheader()
        w.writerows({k: r[k] for k in w.fieldnames} for r in rows)

    # 묶음 효과와 탄소
    base_major = before <= CUT
    base_jobs = job_reach(net, net.graph, fnodes, femp, n)
    # 탐욕 선택: 앞서 고른 지점을 이은 망에서 추가 효과가 가장 큰 곳을 하나씩 고른다(효과가 같은 쌍 중복 방지)
    chosen, g = [], net.graph
    greedy_rows = []
    for step in range(20):
        rv = g.T.tocsr(); dg = net.distances(mask, limit=CUT, graph=g); bef = dg[onodes]
        best = None
        for r in rows:
            if r['link'] in chosen:
                continue
            a, b, L = r['link']
            du = dijkstra(rv, directed=True, indices=a, limit=CUT)[onodes]
            dv = dijkstra(rv, directed=True, indices=b, limit=CUT)[onodes]
            aft = np.minimum(bef, np.minimum(du+L+dg[b], dv+L+dg[a]))
            gain = float(wpop[(bef > CUT) & (aft <= CUT)].sum())
            if best is None or gain > best[0]:
                best = (gain, r)
        if best is None or best[0] <= 0:
            break
        chosen.append(best[1]['link']); g = net.with_links(chosen)
        greedy_rows.append(dict(step=step+1, marginal_workers=round(best[0]),
                                **{k:v for k,v in best[1].items() if k not in ('link','rank','gained_workers')}))
    with open(OUT/'gap_priority_greedy.csv', 'w', encoding='utf-8', newline='') as f:
        fields = list(greedy_rows[0]) if greedy_rows else ['step', 'marginal_workers', *gap_records[0]]
        w = csv.DictWriter(f, fields); w.writeheader(); w.writerows(greedy_rows)
    print('탐욕 선택', [(r['step'], r['gap_m'], r['marginal_workers']) for r in greedy_rows], flush=True)
    record = [c for c, r in zip(chosen, greedy_rows) if r.get('pair') == 'OSM-공식']  # 두 지도가 어긋나 기록만 빠졌을 수 있는 곳
    bundles = [('탐욕 상위 10곳', chosen[:10]), ('탐욕 상위 20곳', chosen[:20]),
               ('탐욕 상위 20곳 중 두 지도 어긋남 제외', [c for c in chosen[:20] if c not in record]),
               ('20m 이내 전부', [r['link'] for r in rows if float(r['gap_m_exact']) <= 20]), ('50m 이내 전부', [r['link'] for r in rows])]
    out = []
    for name, ls in bundles:
        g = net.with_links(ls)
        dd = net.distances(mask, limit=CUT, graph=g)[onodes]
        gained = (~base_major) & (dd <= CUT)
        jobs = job_reach(net, g, fnodes, femp, n)
        new = float(wpop[gained].sum())
        med, mean, person_m = weighted_distance_stats(dd, wpop, gained)
        row = dict(bundle=name, links=len(ls), link_length_m=round(sum(l[2] for l in ls)),
                   new_workers_major_15min=round(new), share_before=round(float(wpop[base_major].sum()/wpop.sum()), 4),
                   share_after=round(float(wpop[dd <= CUT].sum()/wpop.sum()), 4),
                   jobs_avg_before=round(float((wpop*base_jobs).sum()/wpop.sum()), 1), jobs_avg_after=round(float((wpop*jobs).sum()/wpop.sum()), 1),
                   median_oneway_km=round(med/1000, 2), mean_oneway_km=round(mean/1000, 4), carbon_distance_basis='근로연령 인구 가중 거리합')
        for p in (0.05, 0.10, 0.20):
            for k, ef in EFS.items():
                row[f'tCO2_{int(p*100)}%_{k}'] = round(person_m*CAR_SHARE*p*2/1000*250*ef/1000, 1)
        out.append(row)
        print(row, flush=True)
    # 공장 좌표·접속 거리 전체를 유지하고 공장 쪽 반경만 300m로 넓힌다.
    feats = m.load_features(WORK, 'union')
    net2 = ca.factory_access_network(feats, oxy, np.vstack([dests[['x', 'y']].to_numpy(float), fxy]), nd)
    for name, ls in (('공장 진입 구간(150~300m) 연결', []), ('공장 진입 구간 + 탐욕 상위 20곳', chosen[:20])):
        selected_records = [r for r in greedy_rows[:20]] if ls else []
        mapped = map_gap_records(net2, selected_records)
        g2 = net2.with_links(mapped) if mapped else net2.graph
        dd = net2.distances(mask, limit=CUT, graph=g2)[net2.origin_nodes[:n]]
        gained = (~base_major) & (dd <= CUT)
        new = float(wpop[gained].sum()); med, mean, person_m = weighted_distance_stats(dd, wpop, gained)
        jobs = job_reach(net2, g2, net2.destination_nodes[nd:], femp, n)
        row = dict(bundle=name, links=len(ls), link_length_m=round(sum(l[2] for l in ls)), new_workers_major_15min=round(new),
                   share_before=round(float(wpop[base_major].sum()/wpop.sum()), 4), share_after=round(float(wpop[dd <= CUT].sum()/wpop.sum()), 4),
                   jobs_avg_before=round(float((wpop*base_jobs).sum()/wpop.sum()), 1), jobs_avg_after=round(float((wpop*jobs).sum()/wpop.sum()), 1),
                   median_oneway_km=round(med/1000, 2), mean_oneway_km=round(mean/1000, 4), carbon_distance_basis='근로연령 인구 가중 거리합')
        for p in (0.05, 0.10, 0.20):
            for k, ef in EFS.items():
                row[f'tCO2_{int(p*100)}%_{k}'] = round(person_m*CAR_SHARE*p*2/1000*250*ef/1000, 1)
        out.append(row); print(row, flush=True)
    with open(OUT/'gap_bundle_carbon.csv', 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, out[0].keys()); w.writeheader(); w.writerows(out)
    print(f'완료 {time.time()-t0:.0f}s', flush=True)


if __name__ == '__main__':
    main()
