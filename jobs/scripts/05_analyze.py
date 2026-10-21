"""채용공고 자차 조건 집계: 구별·직종별·근무지에서 자전거망·버스 정류소까지 거리별.

분석 대상: 고용24에 직접 등록된(VALIDATION) 화성시 게시 공고. 민간 포털 연계 공고는 본문·우대조건이 없어 제외한다.
지표(공고 비율):
  car_any      자차 조건(필수·우대)이 있는 공고
  car_nonwork  그중 차가 업무 자체에 필요한 경우(방문·배송·영업 등)를 뺀 공고 = 차가 출퇴근·지원 조건으로 작동할 수 있는 공고
               (업무 = 개인 차량 업무 이동이 적힌 공고 + 직무가 근무지 밖 이동인데 회사 차량 언급이 없는 업무추정 공고)
  민감도       업무라고 명시된 공고만 뺀 값(car_nonwork_explicit)
  car_commute  출퇴근·대중교통 불편을 이유로 적은 공고
근무지 거리: 공고의 근무 예정지 좌표(고용24 지도 좌표) → 복원한 합집합 자전거망(측면 접속 가능한 노선), GBIS 노선 정류소.
결과는 집계만 results/에 쓴다. 공고별 원문·좌표는 JOBS_DATA(비공개)에만 둔다.
"""
import csv, json, math, os, random, re, sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import shapely
import shapely.geometry
from pyproj import Transformer

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parent
sys.path.insert(0, str(REPO))
DATA = Path(os.environ.get('JOBS_DATA', 'jobs_private'))
OUT = Path(os.environ.get('JOBS_OUT', HERE / 'results'))
FIG = Path(os.environ.get('JOBS_FIG', HERE / 'figures'))
NET_ROOT = Path(os.environ.get('JOBS_NET_ROOT', REPO / 'outputs/runs/loo_work'))  # 노드 ID 복원 OSM이 든 작업 폴더
GBIS = Path(os.environ.get('JOBS_GBIS', DATA / 'gbis'))  # 정류소(STATION_ID, X, Y)·노선경유(STATION_ID, ROUTE_ID) CSV
COMMUTE_RES = Path(os.environ.get('JOBS_COMMUTE_RESULTS', REPO / 'commute/results'))
GU = ['만세구', '효행구', '병점구', '동탄구']
WEST = {'만세구', '효행구'}

OCC_RULES = [  # 고용24 '모집 직종' 이름 → 직종군 (위에서부터 처음 맞는 것)
    ('운전·운송·배송', r'운전원|운전기사|배송|택배|납품'),
    ('돌봄·보건·복지', r'요양|보육|사회복지|간호|간병|치료사|의료|복지|어린이집|유치원 교사|활동지원'),
    ('사무·기술', r'사무원|사무 보조|관리자|기획|회계|경리|총무|제도사|캐드|연구원|개발자|설계|디자이너|기술자|엔지니어|상담원'),
    ('생산·제조', r'제조|생산|조작원|조립|가공|용접|도장|검사원|포장|사상|금형|절단|프레스|사출|압출|선반|머시닝|CNC|단순 종사원|제관|도금|열처리|주조|단조|연마|재단|봉제|제품'),
    ('건설·설치·정비', r'설치|정비|수리|건설|배관|전기공|보수원|영선|시공|토목|도배|전기원'),
    ('영업·판매', r'영업|판매|매장|계산원|텔레마케터'),
    ('음식·청소·경비', r'조리|주방|급식|청소|경비|미화|주차|세탁|서빙|음식|환경'),
]



def occ_group(name):
    for g, pat in OCC_RULES:
        if re.search(pat, name or ''):
            return g
    return '기타'


def wilson(k, n, z=1.96):
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def mantel_haenszel(rows, exposed, outcome, stratum):
    """층별(맨텔-헨젤) 승산비와 95% 신뢰구간(Robins-Breslow-Greenland 분산). 문자열 '1.66 (1.21-2.27)'로 돌려준다."""
    R = S = PR = PS_QR = QS = 0.0
    for s in set(stratum(r) for r in rows):
        g = [r for r in rows if stratum(r) == s]
        n = len(g)
        a = sum(1 for r in g if exposed(r) and outcome(r)); b = sum(1 for r in g if exposed(r) and not outcome(r))
        c = sum(1 for r in g if not exposed(r) and outcome(r)); d = sum(1 for r in g if not exposed(r) and not outcome(r))
        if n < 2:
            continue
        r_, s_, p_, q_ = a * d / n, b * c / n, (a + d) / n, (b + c) / n
        R += r_; S += s_; PR += p_ * r_; PS_QR += p_ * s_ + q_ * r_; QS += q_ * s_
    if not R or not S:
        return 'nan'
    orv = R / S
    se = math.sqrt(PR / (2 * R * R) + PS_QR / (2 * R * S) + QS / (2 * S * S))
    return f'{orv:.2f} ({math.exp(math.log(orv) - 1.96 * se):.2f}-{math.exp(math.log(orv) + 1.96 * se):.2f})'


def load_bike_lines():
    from analysis.routing import load_features
    feats = [f for f in load_features(NET_ROOT, 'union') if f.lateral_access]
    return shapely.STRtree([f.geometry for f in feats])


def load_stops():
    served = set()
    with open(GBIS / 'route_station.csv', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            served.add(r['STATION_ID'])
    pts = []
    with open(GBIS / 'stations.csv', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            if r['STATION_ID'] in served and r['X'] and r['Y']:
                pts.append((float(r['X']), float(r['Y'])))
    tf = Transformer.from_crs(4326, 5186, always_xy=True)
    xy = np.array([tf.transform(x, y) for x, y in pts])
    return shapely.STRtree(shapely.points(xy))


def band(d, cuts, labels):
    if d is None or math.isnan(d):
        return '좌표 없음'
    for c, lab in zip(cuts, labels):
        if d <= c:
            return lab
    return labels[-1]


def summarise(rows, key, order=None):
    groups = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    keys = order or sorted(groups)
    out = []
    for k in keys:
        g = groups.get(k, [])
        n = len(g)
        rec = dict(group=k, n=n)
        for m in ('pref_car_struct', 'car_any', 'car_nonwork', 'car_commute', 'car_must_nonwork', 'shuttle', 'transit_barrier'):
            c = sum(1 for r in g if r[m])
            lo, hi = wilson(c, n)
            rec[m] = c
            rec[m + '_share'] = round(c / n, 4) if n else ''
            if m in ('car_nonwork', 'car_any'):
                rec[m + '_ci'] = f'{lo:.3f}-{hi:.3f}' if n else ''
        out.append(rec)
    return out


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main():
    posts = [json.loads(l) for l in open(DATA / 'postings.jsonl', encoding='utf-8')]
    posts = [p for p in posts if p['info_type'] == 'VALIDATION']
    labels = {}
    if (DATA / 'labels_final.jsonl').exists():
        labels = {r['id']: r for r in map(json.loads, open(DATA / 'labels_final.jsonl', encoding='utf-8'))}
    flags = {r['id']: r for r in map(json.loads, open(DATA / 'postings_flags.jsonl', encoding='utf-8'))}
    bike, stops = load_bike_lines(), load_stops()
    tf = Transformer.from_crs(4326, 5186, always_xy=True)
    fc = json.loads((NET_ROOT / 'data/raw/admin/hwaseong_hjd_20260701.geojson').read_text(encoding='utf-8'))
    to5186 = lambda c: np.column_stack(tf.transform(c[:, 0], c[:, 1]))
    city = shapely.union_all([shapely.transform(shapely.geometry.shape(f['geometry']), to5186)
                              for f in fc['features'] if '화성시' in f['properties'].get('sggnm', '')]).buffer(1000)
    shapely.prepare(city)
    rows = []
    for p in posts:
        lab = labels.get(p['id'])
        cand = flags[p['id']]['cand']
        if cand and lab is None:
            raise SystemExit(f'후보 공고 라벨 없음: {p["id"]}')
        lv = lab['car_level'] if lab else '없음'
        pu = lab['car_purpose'] if lab else '해당없음'
        r = dict(id=p['id'], gu=p['gu'] if p['gu'] in GU else '구 미상', occ=occ_group(p['occupation']),
                 pref_car_struct=p['flag_pref_car'], car_any=lv in ('필수', '우대'),
                 car_nonwork=lv in ('필수', '우대') and pu != '업무', car_commute=lv in ('필수', '우대') and pu == '출퇴근',
                 car_must_nonwork=lv == '필수' and pu != '업무',
                 car_nonwork_explicit=lv in ('필수', '우대') and not (pu == '업무' and not lab.get('work_inferred')),
                 shuttle=bool(p['benefits'].get('통근버스')) or (lab is not None and lab['shuttle'] == '예'),
                 transit_barrier=lab is not None and lab['transit_barrier'] == '예',
                 night=lab is not None and lab['night_or_shift'] == '예', lv=lv, pu=pu,
                 work_inferred=bool(lab and lab.get('work_inferred')), d_bike=math.nan, d_stop=math.nan)
        if p.get('lon'):
            x, y = tf.transform(p['lon'], p['lat'])
            pt = shapely.Point(x, y)
            if city.contains(pt):  # 화성시 경계(1km 여유) 밖에 찍힌 좌표는 지도 좌표 오류로 보고 쓰지 않는다
                _, d = bike.query_nearest(pt, return_distance=True)
                r['d_bike'] = float(d[0])
                _, d = stops.query_nearest(pt, return_distance=True)
                r['d_stop'] = float(d[0])
        rows.append(r)
    OUT.mkdir(parents=True, exist_ok=True)
    n = len(rows)
    by_gu = summarise(rows, lambda r: r['gu'], GU + ['구 미상'])
    access = {}
    with open(COMMUTE_RES / 'commute_access.csv', encoding='utf-8-sig') as f:
        for a in csv.DictReader(f):
            if a['network'] == 'union' and float(a['cutoff_m']) == 3750:
                access[a['area'] + '구'] = float(a['bike_share_of_straight'])
    fdist = {}
    with open(COMMUTE_RES / 'factory_distance_to_bike.csv', encoding='utf-8-sig') as f:
        for a in csv.DictReader(f):
            fdist[a['area'] + '구'] = float(a['within150'])
    for b in by_gu:
        b['bike_job_access_share'] = access.get(b['group'], '')
        b['factory_jobs_within150m'] = fdist.get(b['group'], '')
    write_csv(OUT / 'jobs_by_gu.csv', by_gu)
    prod = [r for r in rows if r['occ'] == '생산·제조']
    write_csv(OUT / 'jobs_by_gu_production.csv', summarise(prod, lambda r: r['gu'], GU + ['구 미상']))
    write_csv(OUT / 'jobs_by_occupation.csv', summarise(rows, lambda r: r['occ']))
    write_csv(OUT / 'jobs_by_west_east.csv', summarise(rows, lambda r: '서부(만세·효행)' if r['gu'] in WEST else (
        '동부(병점·동탄)' if r['gu'] in GU else '구 미상'), ['서부(만세·효행)', '동부(병점·동탄)', '구 미상']))
    bike_labels = ['150m 이내', '150~300m', '300m~1km', '1km 초과']
    write_csv(OUT / 'jobs_by_bike_distance.csv', summarise(
        rows, lambda r: band(r['d_bike'], [150, 300, 1000, 1e9], bike_labels), bike_labels + ['좌표 없음']))
    stop_labels = ['300m 이내', '300~500m', '500m~1km', '1km 초과']
    write_csv(OUT / 'jobs_by_stop_distance.csv', summarise(
        rows, lambda r: band(r['d_stop'], [300, 500, 1000, 1e9], stop_labels), stop_labels + ['좌표 없음']))
    write_csv(OUT / 'jobs_by_bike_distance_production.csv', summarise(
        prod, lambda r: band(r['d_bike'], [150, 300, 1000, 1e9], ['150m 이내', '150~300m', '300m~1km', '1km 초과']),
        ['150m 이내', '150~300m', '300m~1km', '1km 초과', '좌표 없음']))
    pw = [r for r in prod if r['gu'] in WEST]; pe = [r for r in prod if r['gu'] in GU and r['gu'] not in WEST]
    known = [r for r in rows if r['gu'] in GU]
    mh = mantel_haenszel(known, lambda r: r['gu'] in WEST, lambda r: r['car_nonwork'], lambda r: r['occ'])
    mh_explicit = mantel_haenszel(known, lambda r: r['gu'] in WEST, lambda r: r['car_nonwork_explicit'], lambda r: r['occ'])
    geo = [r for r in rows if not math.isnan(r['d_bike'])]
    mh_bike = mantel_haenszel(geo, lambda r: r['d_bike'] > 300, lambda r: r['car_nonwork'], lambda r: r['occ'])
    mh_bike_stop = mantel_haenszel(geo, lambda r: r['d_bike'] > 300, lambda r: r['car_nonwork'],
                                   lambda r: (r['occ'], r['d_stop'] <= 500))
    # 150m = 자전거 도달 계산에서 일터가 노선에 접속한다고 본 거리
    mh150 = mantel_haenszel(geo, lambda r: r['d_bike'] > 150, lambda r: r['car_nonwork'], lambda r: r['occ'])
    mh150_stop = mantel_haenszel(geo, lambda r: r['d_bike'] > 150, lambda r: r['car_nonwork'],
                                 lambda r: (r['occ'], r['d_stop'] <= 300))
    mh150_region = mantel_haenszel(geo, lambda r: r['d_bike'] > 150, lambda r: r['car_nonwork'],
                                   lambda r: (r['occ'], r['gu'] in WEST))  # 서부·동부 차이를 뺀 같은 권역 안 비교
    near = [r for r in geo if r['d_bike'] <= 150]; far = [r for r in geo if r['d_bike'] > 150]
    method = dict(postings=n, with_coords=len(geo), candidates=sum(1 for p in posts if flags[p['id']]['cand']),
                  pref_car_struct=sum(r['pref_car_struct'] for r in rows), car_any=sum(r['car_any'] for r in rows),
                  car_nonwork=sum(r['car_nonwork'] for r in rows), car_commute=sum(r['car_commute'] for r in rows),
                  car_must_nonwork=sum(r['car_must_nonwork'] for r in rows),
                  car_any_without_struct_flag=sum(1 for r in rows if r['car_any'] and not r['pref_car_struct']),
                  struct_flag_but_work=sum(1 for r in rows if r['pref_car_struct'] and r['pu'] == '업무'),
                  mh_or_west_vs_east_by_occ=mh, mh_or_bike_over300_by_occ=mh_bike,
                  work_inferred=sum(1 for r in rows if r['lv'] in ('필수', '우대') and r['work_inferred']),
                  explicit_work_only_sensitivity=dict(
                      car_nonwork_explicit=sum(r['car_nonwork_explicit'] for r in rows),
                      west=sum(1 for r in known if r['gu'] in WEST and r['car_nonwork_explicit']),
                      east=sum(1 for r in known if r['gu'] not in WEST and r['car_nonwork_explicit']),
                      mh_or_west_vs_east_by_occ=mh_explicit),
                  mh_or_bike_over300_by_occ_and_stop500=mh_bike_stop,
                  mh_or_bike_over150_by_occ=mh150, mh_or_bike_over150_by_occ_and_stop300=mh150_stop,
                  mh_or_bike_over150_by_occ_and_region=mh150_region,
                  bike_within150=dict(n=len(near), car_nonwork=sum(r['car_nonwork'] for r in near)),
                  bike_over150=dict(n=len(far), car_nonwork=sum(r['car_nonwork'] for r in far)),
                  production_west=dict(n=len(pw), car_nonwork=sum(r['car_nonwork'] for r in pw)),
                  production_east=dict(n=len(pe), car_nonwork=sum(r['car_nonwork'] for r in pe)),
                  west=dict(n=sum(1 for r in known if r['gu'] in WEST), car_nonwork=sum(1 for r in known if r['gu'] in WEST and r['car_nonwork'])),
                  east=dict(n=sum(1 for r in known if r['gu'] not in WEST), car_nonwork=sum(1 for r in known if r['gu'] not in WEST and r['car_nonwork'])),
                  rule_vs_ai_purpose_agreement=None)
    # 규칙 기반 분류와 AI 최종 라벨 비교(후보 공고만)
    cmp_rows = [(flags[r['id']]['rule_car_level'], flags[r['id']]['rule_car_purpose'], r['lv'], r['pu'])
                for r in rows if flags[r['id']]['cand']]
    if cmp_rows:
        method['rule_vs_ai_level_agreement'] = round(sum(a == c for a, b, c, d in cmp_rows) / len(cmp_rows), 4)
        method['rule_vs_ai_purpose_agreement'] = round(sum(b == d for a, b, c, d in cmp_rows) / len(cmp_rows), 4)
        method['rule_commute'] = sum(1 for a, b, c, d in cmp_rows if a != '없음' and b == '출퇴근')
        method['rule_work'] = sum(1 for a, b, c, d in cmp_rows if a != '없음' and b == '업무')
        method['ai_work'] = sum(1 for a, b, c, d in cmp_rows if c != '없음' and d == '업무')
    (OUT / 'jobs_method_summary.json').write_text(json.dumps(method, ensure_ascii=False, indent=1), encoding='utf-8')
    # 사람 검증용 무작위 100건(후보 공고, 고정 시드) — 원문 포함이라 비공개 폴더에만 쓴다
    cand_ids = sorted(p['id'] for p in posts if flags[p['id']]['cand'])
    pick = random.Random(1022).sample(cand_ids, min(100, len(cand_ids)))
    src = {}
    for bp in sorted((DATA / 'batches').glob('batch_*.json')):
        for s in json.loads(bp.read_text(encoding='utf-8')):
            src[s['id']] = s
    with open(DATA / 'verify_sheet_100.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['id', 'title', 'occupation', 'pref_condition', 'text', 'ai_car_level', 'ai_car_purpose',
                    'ai_evidence', 'human_car_level(필수/우대/없음)', 'human_car_purpose(출퇴근/업무/불명/해당없음)', 'memo'])
        by_id = {q['id']: q for q in posts}
        for i in pick:
            q, l = by_id[i], labels.get(i, {})
            parts = [('본문', q['body']), ('기타 우대사항', q['pref_other']), ('그 밖의 희망사항', q.get('other_wish', '')),
                     ('기타 복리후생', q['benefit_other']), ('근무시간', q['work_hours'])]
            text = ' | '.join(f'[{k}] {v}' for k, v in parts if v and v.strip() not in ('', '-'))
            pu = l.get('car_purpose', '')
            if pu == '업무' and l.get('work_inferred'):
                pu = '업무(추정)'
            w.writerow([i, q['title'], q['occupation'], q['pref_condition'], text[:4000],
                        l.get('car_level', ''), pu, l.get('evidence', ''), '', '', ''])
    print(json.dumps(method, ensure_ascii=False))
    for b in by_gu:
        print(b['group'], b['n'], b['pref_car_struct_share'], b['car_any_share'], b['car_nonwork_share'], b['car_commute_share'],
              b['shuttle_share'], b['bike_job_access_share'])


if __name__ == '__main__':
    main()
