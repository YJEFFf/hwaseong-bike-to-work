"""고용24 화성시 채용공고 수집: 목록 전체 → 상세 원문.

- 목록: 근무지역 화성시(41590) 게시 중 공고 전체를 100건씩 받아 list.csv로 저장
- 상세: 공고마다 상세 화면 원문을 한 번만 받아 detail_html/<공고번호>.html.gz로 저장(이미 있으면 건너뜀)
- 요청 간격: 작업자마다 1초 이상, 작업자 3명. robots.txt는 /wk/ 경로를 허용한다.
- 원문에는 채용 담당자 연락처가 있으므로 JOBS_DATA(비공개 폴더)에만 둔다. 레포에는 올리지 않는다.
실행: JOBS_DATA=<비공개 폴더> python -X utf8 jobs/scripts/01_collect.py [list|detail|all]
"""
import csv, gzip, html, os, re, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import requests

DATA = Path(os.environ.get('JOBS_DATA', 'jobs_private'))
HTML_DIR = DATA / 'detail_html'
HTML_DIR.mkdir(parents=True, exist_ok=True)
BASE = 'https://www.work24.go.kr'
LIST_URL = BASE + '/wk/a/b/1200/retriveDtlEmpSrchList.do'
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) research-noncommercial'}
REGION = os.environ.get('JOBS_REGION', '41590')
PER_PAGE = 100
WORKERS = 3
LOG = DATA / 'collect_log.txt'
_lock = threading.Lock()


def log(msg):
    line = f'{datetime.now():%Y-%m-%d %H:%M:%S} {msg}'
    with _lock:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    print(line, flush=True)


def get(url, params=None, tries=4):
    for k in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=30)
            if r.status_code == 200 and len(r.text) > 2000:
                return r.text
            log(f'retry {k + 1}: status {r.status_code} len {len(r.text)} {url}')
        except requests.RequestException as e:
            log(f'retry {k + 1}: {e!r} {url}')
        time.sleep(3 * (k + 1))
    return None


def parse_list(page_html):
    rows = []
    parts = re.split(r'id="chkboxWantedAuthNo\d+"', page_html)[1:]
    for seg in parts:
        m = re.search(r'value="([^"]+)"', seg)
        if not m:
            continue
        wid, info_type, company, title = (html.unescape(m.group(1)).split('|') + ['', '', '', ''])[:4]
        href = re.search(r'href="(/wk/a/b/1500/[^"]+)"', seg)
        loc = re.search(r'경기도 화성시[^<|]{0,40}', html.unescape(re.sub(r'<[^>]+>', '|', seg)))
        close = re.search(r"var date = '([^']*)'", seg)
        reg = re.search(r'등록일\s*:\s*([\d-]+)', seg)
        rows.append(dict(wanted_auth_no=wid, info_type=info_type, company=company, title=title,
                         href=html.unescape(href.group(1)) if href else '',
                         location=re.sub(r'\s+', ' ', loc.group(0)).strip() if loc else '',
                         close_date=close.group(1) if close else '', reg_date=reg.group(1) if reg else ''))
    return rows


def collect_list():
    first = get(LIST_URL, dict(region=REGION, resultCnt=PER_PAGE, pageIndex=1, sortOrderBy='DESC', sortField='DATE'))
    total = int(re.search(r'txt_total">\s*([\d,]+)', first).group(1).replace(',', ''))
    pages = (total + PER_PAGE - 1) // PER_PAGE
    log(f'list: total {total}, pages {pages}')
    rows = parse_list(first)
    for p in range(2, pages + 1):
        time.sleep(1.0)
        page = get(LIST_URL, dict(region=REGION, resultCnt=PER_PAGE, pageIndex=p, sortOrderBy='DESC', sortField='DATE'))
        if page is None:
            log(f'list page {p} failed')
            continue
        got = parse_list(page)
        rows.extend(got)
        if p % 10 == 0:
            log(f'list page {p}/{pages}: {len(rows)} rows')
    seen, uniq = set(), []
    for r in rows:
        if r['wanted_auth_no'] and r['wanted_auth_no'] not in seen:
            seen.add(r['wanted_auth_no'])
            uniq.append(r)
    with open(DATA / 'list.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(uniq[0]))
        w.writeheader()
        w.writerows(uniq)
    (DATA / 'list_meta.txt').write_text(f'collected_at={datetime.now().isoformat()}\nregion={REGION}\n'
                                        f'site_total={total}\nunique_rows={len(uniq)}\n', encoding='utf-8')
    log(f'list done: {len(rows)} rows, {len(uniq)} unique (site total {total})')
    return uniq


def fetch_detail(row):
    out = HTML_DIR / f"{row['wanted_auth_no']}.html.gz"
    if out.exists():
        return 'skip'
    url = BASE + row['href'] if row['href'] else (
        f"{BASE}/wk/a/b/1500/empDetailAuthView.do?wantedAuthNo={row['wanted_auth_no']}"
        f"&infoTypeCd={row['info_type']}&infoTypeGroup=tb_workinfoworknet")
    page = get(url)
    time.sleep(1.0)
    if page is None:
        return 'fail'
    with gzip.open(out, 'wt', encoding='utf-8') as f:
        f.write(page)
    return 'ok'


def collect_detail(rows):
    done = {'ok': 0, 'skip': 0, 'fail': 0}
    with ThreadPoolExecutor(WORKERS) as ex:
        for i, res in enumerate(ex.map(fetch_detail, rows), 1):
            done[res] += 1
            if i % 200 == 0:
                log(f'detail {i}/{len(rows)}: {done}')
    log(f'detail done: {done}')


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if mode in ('list', 'all'):
        rows = collect_list()
    else:
        with open(DATA / 'list.csv', encoding='utf-8-sig') as f:
            rows = list(csv.DictReader(f))
    if mode in ('detail', 'all'):
        # 민간 포털 연계 공고는 고용24 상세에 본문·우대조건·구가 없어 분석에 쓰지 않는다
        collect_detail([r for r in rows if r['info_type'] == 'VALIDATION'])
