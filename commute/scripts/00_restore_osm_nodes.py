"""S0 OSM 자전거 파일의 빠진 노드 번호 복원 (commute 파이프라인 첫 단계).

2026-09-14 Overpass 추출 파일(osm_hwaseong_bike_20260914.json)에는 도로 형상만 있고 도로끼리 공유하는
노드 번호(nodes)가 빠져 있다. analysis/routing.py는 높이가 다른 도로(교량·터널)를 이 번호로만 잇기 때문에,
번호가 없으면 교량·터널 구간이 모두 끊긴 것으로 계산된다(덩어리 383개 → 복원 후 208개).

규칙(작업 레포 Codex_작업기록/20_policy_comparison_1021/recover_osm_api.py를 레포용으로 옮김):
- 추출 시점(osm3s timestamp) 이전의 마지막 도로 버전만 쓴다. 이후에 바뀐 도로는 이력 API로 그 시점 버전을 찾는다.
- 태그와 노드 수가 저장본과 같을 때만 nodes를 붙인다. 저장된 형상·태그는 바꾸지 않는다.
- 여러 도로가 공유하는 노드의 좌표가 저장본에서 서로 다르면 멈춘다.
실행: 저장소 루트에서 `python -X utf8 commute/scripts/00_restore_osm_nodes.py [작업 폴더]`
      (기본 outputs/runs/loo_work). 원본은 같은 폴더에 *_without_nodes.json으로 남긴다.
"""
import hashlib, json, shutil, sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

API = 'https://api.openstreetmap.org/api/0.6'
NAME = 'osm_hwaseong_bike_20260914.json'


def main(work):
    raw = work / 'data/raw'
    src = raw / NAME
    data = json.loads(src.read_text(encoding='utf-8'))
    ways = [e for e in data['elements'] if e['type'] == 'way']
    if ways and all(e.get('nodes') for e in ways):
        print('이미 노드 번호가 있음, 건너뜀')
        return
    ts = data['osm3s']['timestamp_osm_base']
    cache = raw / 'osm_api_cache'
    cache.mkdir(exist_ok=True)
    ids = [e['id'] for e in ways]

    def fetch(chunk):
        p = cache / f'ways_{chunk[0]}.json'
        if not p.exists():
            r = requests.get(f'{API}/ways.json?ways=' + ','.join(map(str, chunk)), timeout=40)
            r.raise_for_status()
            p.write_bytes(r.content)
        return json.loads(p.read_text(encoding='utf-8'))['elements']

    with ThreadPoolExecutor(max_workers=2) as pool:
        found = {e['id']: e for part in pool.map(fetch, [ids[i:i + 150] for i in range(0, len(ids), 150)]) for e in part}

    def historical(way_id):
        p = cache / f'history_{way_id}.json'
        if not p.exists():
            r = requests.get(f'{API}/way/{way_id}/history.json', timeout=40)
            r.raise_for_status()
            p.write_bytes(r.content)
        past = [e for e in json.loads(p.read_text(encoding='utf-8'))['elements']
                if e['timestamp'] <= ts and e.get('visible', True)]
        return way_id, max(past, key=lambda e: e['version']) if past else None

    changed = [i for i, e in found.items() if e['timestamp'] > ts]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for i, e in pool.map(historical, changed):
            if e is not None:
                found[i] = e
    matched, failed = [], []
    for el in ways:
        c = found.get(el['id'])
        if c and c['timestamp'] <= ts and el.get('tags', {}) == c.get('tags', {}) and len(c.get('nodes', [])) == len(el.get('geometry', [])):
            el['nodes'] = c['nodes']
            matched.append(el['id'])
        else:
            failed.append(el['id'])
    coords, conflicts = {}, []
    for el in ways:
        for ref, pt in zip(el.get('nodes', []), el['geometry']):
            xy = (pt['lon'], pt['lat'])
            if ref in coords and coords[ref] != xy:
                conflicts.append(ref)
            coords[ref] = xy
    if conflicts:
        raise SystemExit(f'공유 노드 좌표 불일치 {len(conflicts)}건: 복원하지 않음')
    shutil.copy2(src, raw / NAME.replace('.json', '_without_nodes.json'))
    src.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    report = dict(snapshot_timestamp=ts, retrieved_utc=datetime.now(timezone.utc).isoformat(), ways=len(ways),
                  restored=len(matched), history_lookups=len(changed), unmatched=failed,
                  restored_sha256=hashlib.sha256(src.read_bytes()).hexdigest())
    (raw / 'osm_node_restore.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'unmatched'} | {'unmatched': len(failed)}, ensure_ascii=False))


if __name__ == '__main__':
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd() / 'outputs/runs/loo_work')
