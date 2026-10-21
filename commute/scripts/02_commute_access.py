"""근로 연령(20~64세) 인구의 공장 일자리 자전거 도달 — 직선거리 대비 자전거망 거리.

- 출발지: 제출레포 loo_work의 100m 격자 인구 × 동별 20~64세 비율(동 안 균등 가정)
- 목적지: 공장등록 2024-02(종업원 수 가중), 브이월드 좌표(PRIVATE, 로컬 전용)
- 망: osm·official·union·lowstress, 접속 150m, 망 거리 2.5/3.75/5km(시속 15km로 10/15/20분)
- 끝점 연결 보정: union에 10·20m bridge_links를 더한 시나리오
출력은 구·동 집계만(좌표·개별 공장 없음). 실행: 제출레포 루트에서 `python -X utf8 <이 파일>`. 저장소 코드 수정 없음.
"""
import csv, importlib.util, io, os, pathlib, time
from pathlib import Path
import numpy as np
import openpyxl
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

ROOT = Path.cwd(); WORK = ROOT/'outputs/runs/loo_work'
HERE = Path(__file__).resolve().parent.parent
def _factory_dir():
    """공장 원자료·좌표 폴더: FACTORY_DIR → 작업 레포의 private_data → 예전 개인 백업 순서로 처음 있는 곳."""
    import os as _os
    here = pathlib.Path(__file__).resolve().parent
    cands = [_os.environ.get('FACTORY_DIR'),
             here.parent/'private_data/factory_202402',  # 작업 레포 13_재설계_출퇴근_0927/scripts 에서 실행할 때
             pathlib.Path.cwd().parent/'Claude_작업기록/13_재설계_출퇴근_0927/private_data/factory_202402',  # 제출레포가 작업 레포 안에 있을 때
             pathlib.Path.home()/'Documents/hbmg_private_backup/factory_202402']
    for c in cands:
        if c and pathlib.Path(c).is_dir():
            return pathlib.Path(c)
    raise FileNotFoundError('공장 자료 폴더를 찾지 못함: FACTORY_DIR을 지정하거나 작업 레포 private_data/factory_202402를 받으세요')


PRIVATE = _factory_dir()  # 공장 원자료·좌표(비공개, 공개 저장소에 올리지 않음)
OUT = Path(os.environ.get('COMMUTE_OUT', HERE/'results')); OUT.mkdir(parents=True, exist_ok=True)
CUTS = (2500., 3750., 5000.)
ACCESS = float(os.environ.get('ACCESS_M', 150))  # 노선 접속 반경(m), 민감도는 ACCESS_M=300
SUFFIX = '' if ACCESS == 150 else f'_access{ACCESS:.0f}'
spec = importlib.util.spec_from_file_location('auto10', ROOT/'scripts/10_auto_scenarios.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
from commute.audit_support import factory_access_network, map_gap_records, locality_key


def working_age_weights(origins):
    raw = open(WORK/'data/raw/population/pop_raw.bin', 'rb').read().decode('cp949')
    rows = list(csv.reader(io.StringIO(raw))); h = rows[0]
    share = {}
    for r in rows[1:]:
        if '화성시' not in r[3] or not r[4].strip():
            continue
        tot = int(r[5].replace(',', ''))
        share[r[4].strip()] = sum(int(r[h.index(f'{a}세{s}')].replace(',', '')) for a in range(20, 65) for s in ('남자', '여자'))/tot
    w = origins['pop'].to_numpy(float)*origins['dong'].map(share).to_numpy(float)
    if np.isnan(w).any():
        raise ValueError('20~64세 비율이 없는 동')
    return w


def load_factories(report=False, flags=False):
    """좌표: ① 브이월드 지번 변환 ② 수동 보정(PRIVATE/manual_geocode.csv) ③ 같은 산업단지 변환 성공 공장의 평균 위치
    ④ 같은 리·동 변환 성공 공장의 평균 위치(근사). 개발로 지번이 바뀐 산단 공장이 ②~④로 들어온다."""
    geo = {r['address']: r for r in csv.DictReader(open(PRIVATE/'geocode_cache.csv', encoding='utf-8'))}
    manual_path = PRIVATE/'manual_geocode.csv'
    if not manual_path.exists():
        raise FileNotFoundError('동일 수치 재현에는 manual_geocode.csv가 필요합니다. 01/01b만으로 수동 보정 파일은 생성되지 않습니다.')
    manual = list(csv.DictReader(open(manual_path, encoding='utf-8')))
    ws = openpyxl.load_workbook(PRIVATE/'화성_공장등록_202402.xlsx', read_only=True, data_only=True).worksheets[0]
    recs = []
    for r in ws.iter_rows(min_row=3, values_only=True):
        if not r or r[0] in (None, ''):
            continue
        a = (r[7] or '').strip(); g = geo.get(a) or {}
        recs.append(dict(emp=int(str(r[5]).replace(',', '') or 0), dan=(r[2] or '').strip(), ri=locality_key(a),
                         q=g.get('query', ''), ok=g.get('status') == 'OK', x=float(g['x']) if g.get('status') == 'OK' else None,
                         y=float(g['y']) if g.get('status') == 'OK' else None))
    cen_dan, cen_ri = {}, {}
    for r in recs:
        if r['ok']:
            for d, k in ((cen_dan, r['dan']), (cen_ri, r['ri'])):
                if k:
                    d.setdefault(k, []).append((r['x'], r['y']))
    cen_dan = {k: np.mean(v, axis=0) for k, v in cen_dan.items()}; cen_ri = {k: np.mean(v, axis=0) for k, v in cen_ri.items()}
    xs, ys, emp, dan, tier, approx = [], [], [], [], {}, []
    miss = [0, 0]
    for r in recs:
        xy, t = None, None
        if r['ok']:
            xy, t = (r['x'], r['y']), '지번 변환'
        else:
            hit = next((mm for mm in manual if mm['pattern'] in r['q']), None)
            if hit:
                xy, t = (float(hit['x']), float(hit['y'])), '수동 보정'
            elif r['dan'] and r['dan'] in cen_dan:
                xy, t = tuple(cen_dan[r['dan']]), '산단 평균 위치'
            elif r['ri'] in cen_ri:
                xy, t = tuple(cen_ri[r['ri']]), '리·동 평균 위치(근사)'
        if xy is None:
            miss[0] += 1; miss[1] += r['emp']; continue
        tier.setdefault(t, [0, 0]); tier[t][0] += 1; tier[t][1] += r['emp']
        xs.append(xy[0]); ys.append(xy[1]); emp.append(r['emp']); dan.append(bool(r['dan'])); approx.append(t not in ('지번 변환',))
    if report:
        print('좌표 출처:', {k: f'{v[0]}곳·{v[1]}명' for k, v in tier.items()}, '| 제외', miss, flush=True)
    # 같은 주소(같은 좌표)의 공장은 한 점으로 묶는다(종업원 합산)
    agg = {}
    for x, y, e, d, ap in zip(xs, ys, emp, dan, approx):
        a = agg.setdefault((x, y), [0., False, False]); a[0] += e; a[1] = a[1] or d; a[2] = a[2] or ap
    keys = sorted(agg)
    out = (np.array(keys, float), np.array([agg[k][0] for k in keys]), np.array([agg[k][1] for k in keys]), miss)
    if flags:  # 수동 보정·산단/리동 평균 위치가 섞인 점
        return out + (np.array([agg[k][2] for k in keys]),)
    return out


def fixed_gap_links(net, tolerance):
    rows = list(csv.DictReader(open(OUT/'gap_points_50m.csv', encoding='utf-8')))
    if rows and 'gap_m_exact' not in rows[0]:
        raise ValueError('03_gaps_and_shared.py를 먼저 재실행하세요')
    return map_gap_records(net, [r for r in rows if float(r['gap_m_exact']) <= tolerance])


def main():
    t0 = time.time()
    origins, dests = m.prepare_population(WORK)
    oxy = origins[['x', 'y']].to_numpy(float); n = len(oxy)
    wpop = working_age_weights(origins)
    dist = origins['district'].to_numpy(); dong = origins['dong'].to_numpy()
    fxy, femp, fdan, miss = load_factories(report=True)
    print(f'근로 연령 인구 {wpop.sum():.0f}, 공장 좌표 {len(fxy)}곳·종업원 {femp.sum():.0f}명(좌표 실패 {miss[0]}곳·{miss[1]}명)', flush=True)
    station = dests.kind.eq('station').to_numpy()
    scopes = [('화성시', np.ones(n, bool))] + [(k, dist == k) for k in ('동탄', '병점', '효행', '만세')]

    # 직선거리 안 공장 일자리(근로 연령 인구 가중 평균)
    tree = cKDTree(fxy)
    straight = {c: np.zeros(n) for c in CUTS}
    for c in CUTS:
        for i0 in range(0, n, 2000):
            for j, idx in enumerate(tree.query_ball_point(oxy[i0:i0+2000], c)):
                straight[c][i0+j] = femp[idx].sum()

    rows = []
    scen = [('osm', 0.), ('official', 0.), ('union', 0.), ('union', 10.), ('union', 20.), ('union-fac300', 0.), ('union-fac300', 20.), ('lowstress', 0.)]
    nets = {}
    for version, tol in scen:
        if version not in nets:
            feats = m.load_features(WORK, version.split('-')[0])
            dxy = np.vstack([dests[['x', 'y']].to_numpy(float), fxy])
            if version == 'union-fac300':
                nets[version] = factory_access_network(feats, oxy, dxy, len(dests), origin_max=ACCESS,
                                                      base_max=ACCESS, factory_max=max(300., ACCESS))
            else:
                nets[version] = m.build_access_network(feats, oxy, dxy, snap=1., access_max=ACCESS)
        net = nets[version]
        graph = net.with_links(fixed_gap_links(net, tol)) if tol else net.graph
        rev = graph.T.tocsr()
        onodes = net.origin_nodes[:n]
        fnodes = net.destination_nodes[len(dests):]
        attached = np.diff(rev.indptr)[fnodes] > 0  # 노선 150m 안에 접속한 공장 점(역방향 그래프에서 나가는 간선 있음)
        if version == scen[0][0] and not tol:
            print(f'  접속한 공장 점 {attached.sum()}/{len(fnodes)}, 종업원 {femp[attached].sum():.0f}', flush=True)
        reach = {c: np.zeros(n) for c in CUTS}
        for node, e in zip(fnodes[attached], femp[attached]):
            dd = dijkstra(rev, directed=True, indices=int(node), limit=max(CUTS))[onodes]
            for c in CUTS:
                reach[c] += e*(dd <= c)
        st = net.approach_distances(np.r_[station, np.zeros(len(fxy), bool)], max(CUTS), graph)[:n]
        detached = net.detached[:n]
        name = (version.replace('union-fac300', 'union+공장접속300m')) + (f'+끝점{tol:.0f}m' if tol else '')
        for c in CUTS:
            for area, s in scopes:
                w = wpop[s]
                rows.append(dict(network=name, cutoff_m=c, area=area, working_age=round(w.sum()),
                    straight_jobs_avg=round(float((w*straight[c][s]).sum()/w.sum()), 1),
                    bike_jobs_avg=round(float((w*reach[c][s]).sum()/w.sum()), 1),
                    bike_share_of_straight=round(float((w*reach[c][s]).sum()/max((w*straight[c][s]).sum(), 1)), 4),
                    reach_any_1000jobs=round(float(w[reach[c][s] >= 1000].sum()/w.sum()), 4),
                    straight_any_1000jobs=round(float(w[straight[c][s] >= 1000].sum()/w.sum()), 4),
                    station_reach=round(float(w[st[s] <= c].sum()/w.sum()), 4),
                    facility_over_150m=round(float(w[detached[s]].sum()/w.sum()), 4)))
        print(f'{name} 완료 {time.time()-t0:.0f}s', flush=True)
        if version == 'union' and tol == 0.:
            base_reach = reach
    with open(OUT/f'commute_access{SUFFIX}.csv', 'w', encoding='utf-8', newline='') as f:
        wr = csv.DictWriter(f, rows[0].keys()); wr.writeheader(); wr.writerows(rows)
    # 동별(합집합망, 15분) — 지점 우선순위 해석용
    with open(OUT/f'commute_access_by_dong{SUFFIX}.csv', 'w', encoding='utf-8', newline='') as f:
        wr = csv.writer(f); wr.writerow(['dong', 'district', 'working_age', 'straight_jobs_avg_15', 'bike_jobs_avg_15', 'bike_share_of_straight_15'])
        for d in sorted(set(dong)):
            s = dong == d; w = wpop[s]
            wr.writerow([d, dist[s][0], round(w.sum()), round(float((w*straight[3750.][s]).sum()/w.sum()), 1),
                         round(float((w*base_reach[3750.][s]).sum()/w.sum()), 1),
                         round(float((w*base_reach[3750.][s]).sum()/max((w*straight[3750.][s]).sum(), 1)), 4)])
    print('저장', OUT/f'commute_access{SUFFIX}.csv', flush=True)


if __name__ == '__main__':
    main()
