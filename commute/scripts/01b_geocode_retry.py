"""01_geocode_factories.py에서 NOT_FOUND·오류였던 주소만 더 엄격하게 다듬어 다시 조회한다.

다듬기: '…동/리/가 (산)번지' 까지만 남긴다(뒤의 건물 동 번호·호수·설명 제거). 결과는 같은 캐시 파일을 고쳐 쓴다(PRIVATE).
"""
import csv, importlib.util, re
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('geo', HERE/'01_geocode_factories.py')
geo = importlib.util.module_from_spec(spec); spec.loader.exec_module(geo)
PAT = re.compile(r'^(.*?(?:동|리|가)\s*(?:산\s*)?\d+(?:-\d+)?)')


def strict(addr):
    a = geo.clean(addr)
    mt = PAT.match(a)
    return re.sub(r'산\s+(\d)', r'산\1', mt.group(1)).strip() if mt else a


rows = list(csv.DictReader(open(geo.CACHE, encoding='utf-8')))
fixed = 0
for r in rows:
    if r['status'] == 'OK':
        continue
    q = strict(r['address'])
    if q == r['query'] and r['status'] == 'NOT_FOUND':
        continue
    st, x, y, ref = geo.query(q)
    if st == 'OK':
        r.update(query=q, status=st, x=x, y=y, refined=ref); fixed += 1
with open(geo.CACHE, 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, geo.FIELDS); w.writeheader(); w.writerows(rows)
ok = sum(r['status'] == 'OK' for r in rows)
print(f'재시도로 {fixed}건 추가, 최종 {ok}/{len(rows)} 성공')
