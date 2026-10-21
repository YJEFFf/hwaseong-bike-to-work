"""화성시 공장등록(2024-02) 지번 주소를 브이월드 지오코더로 좌표(EPSG:5186) 변환한다.

- 원본(연락처 열 포함)과 변환 결과는 Git에 올리지 않는다. 브이월드 현행 이용약관 제19조의 저장·복제·제공 조건을 확인해야 하며, 로컬 저장 자체가 허가를 뜻하지 않는다.
- 키는 ~/.vworld_key 에서 읽는다. 이미 변환한 주소는 캐시에서 건너뛰므로 중단 후 다시 돌려도 된다.
실행: python -X utf8 01_geocode_factories.py
"""
import csv, json, os, pathlib, re, threading, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

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
SRC = PRIVATE / '화성_공장등록_202402.xlsx'
CACHE = PRIVATE / 'geocode_cache.csv'
KEY = os.environ.get('VWORLD_KEY') or pathlib.Path.home().joinpath('.vworld_key').read_text().strip()
URL = 'https://api.vworld.kr/req/address?'
FIELDS = ['address', 'query', 'status', 'x', 'y', 'refined']


def clean(addr):
    """지번 주소를 지오코더가 읽는 꼴로: '번지'·'외 N필지'·괄호 설명·쉼표 뒤를 뗀다."""
    a = re.sub(r'\(.*?\)', ' ', addr)
    a = a.split(',')[0]
    a = re.split(r'\s외\s*\d*', a)[0]
    a = a.replace('번지', ' ')
    a = re.sub(r'산\s+(\d)', r'산\1', a)
    return re.sub(r'\s+', ' ', a).strip()


def query(q):
    params = dict(service='address', request='getcoord', version='2.0', crs='epsg:5186', address=q,
                  refine='true', simple='false', format='json', type='parcel', key=KEY)
    for attempt in range(3):
        try:
            r = json.load(urllib.request.urlopen(URL + urllib.parse.urlencode(params), timeout=20))['response']
            if r.get('status') == 'OK':
                p = r['result']['point']
                return 'OK', p['x'], p['y'], r.get('refined', {}).get('text', '')
            return r.get('status', 'ERROR'), '', '', json.dumps(r.get('error', ''), ensure_ascii=False)[:120]
        except Exception as e:  # 일시 오류는 잠시 쉬고 다시
            err = repr(e)[:120]
            time.sleep(2 * (attempt + 1))
    return 'EXC', '', '', err


def main():
    import openpyxl
    ws = openpyxl.load_workbook(SRC, read_only=True, data_only=True).worksheets[0]
    addrs = sorted({(r[7] or '').strip() for r in ws.iter_rows(min_row=3, values_only=True) if r and r[0] not in (None, '')})
    done = {}
    if CACHE.exists():
        done = {row['address']: row for row in csv.DictReader(open(CACHE, encoding='utf-8'))}
    todo = [a for a in addrs if a and a not in done]
    print(f'고유 주소 {len(addrs)}, 캐시 {len(done)}, 남은 {len(todo)}', flush=True)
    new = not CACHE.exists()
    f = open(CACHE, 'a', encoding='utf-8', newline='')
    w = csv.DictWriter(f, FIELDS)
    if new:
        w.writeheader()
    lock, count = threading.Lock(), [0]

    def work(a):
        q = clean(a)
        st, x, y, ref = query(q)
        with lock:
            w.writerow(dict(address=a, query=q, status=st, x=x, y=y, refined=ref))
            count[0] += 1
            if count[0] % 500 == 0:
                f.flush(); print(f'{count[0]}/{len(todo)}', flush=True)
        time.sleep(0.1)

    with ThreadPoolExecutor(4) as ex:
        list(ex.map(work, todo))
    f.close()
    rows = list(csv.DictReader(open(CACHE, encoding='utf-8')))
    ok = sum(r['status'] == 'OK' for r in rows)
    print(f'완료: {ok}/{len(rows)} 변환 성공', flush=True)


if __name__ == '__main__':
    main()
