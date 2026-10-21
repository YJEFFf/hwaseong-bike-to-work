"""상세 원문에서 분석 항목을 뽑는다. 채용 담당자 정보(이름·전화·이메일)는 읽지 않고 버린다.

출력: JOBS_DATA/postings.jsonl (공고 1건 = 1줄). 본문의 전화번호·이메일은 지운다.
레포에 올리는 파일(results/)에는 공고번호·구·플래그·집계만 쓴다. 원문은 비공개 폴더에만 둔다.
"""
import csv, gzip, html, json, os, re
from pathlib import Path

DATA = Path(os.environ.get('JOBS_DATA', 'jobs_private'))
LABELS = ['모집 인원', '장애인 채용 인원', '모집 직종', '관련 직종', '직종 키워드', '경력', '학력', '자격 면허',
          '근무조건', '고용 형태', '임금 조건', '근무 시간', '상세 근무시간', '근무 형태', '사회보험', '퇴직급여',
          '근무 예정지', '인근 전철역', '버스 노선 번호', '전공', '컴퓨터 활용 능력', '외국어 능력',
          '우대조건', '기타 우대사항', '기타사항', '그 밖의 희망사항', '복리후생', '기타 복리후생', '장애인용 복지시설', '작업환경',
          '전형방법', '접수 마감일']
COMPANY_LABELS = ['기업명', '업종', '기업규모', '설립연도', '연매출액', '근로자수', '채용정보 이력']
BENEFITS = ['통근버스', '기숙사', '차량유지비', '교육비 지원', '자녀 학자금 지원', '주택자금 지원', '직원대출제도', '모성보호시설']
PHONE = re.compile(r'0\d{1,2}[-.)\s]?\d{3,4}[-.\s]?\d{4}')
EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+')


def clean_text(page):
    # 표 캡션('모집 인원,…을(를) 제공하는 표')은 <caption> 태그째 지운다. 글자로 지우면 쉼표가 든 앞 칸 내용까지 지워진다.
    t = re.sub(r'<script.*?</script>|<style.*?</style>|<!--.*?-->|<caption[^>]*>.*?</caption>', ' ', page, flags=re.S)
    t = html.unescape(re.sub(r'<[^>]+>', ' ', t))
    return re.sub(r'\s+', ' ', t)


def scrub(s):
    return EMAIL.sub('[이메일]', PHONE.sub('[전화]', s)).strip()


def fields_in(text, labels):
    out, pos = {}, 0
    found = []
    for lab in labels:
        i = text.find(' ' + lab + ' ', pos)
        if i >= 0:
            found.append((lab, i + 1, i + 1 + len(lab)))
            pos = i + 1 + len(lab)
    for k, (lab, s, e) in enumerate(found):
        end = found[k + 1][1] if k + 1 < len(found) else min(len(text), e + 400)
        out[lab] = text[e:end].split('『')[0].strip()
    return out


def parse(page):
    t = clean_text(page)
    start = t.find('모집요강 직무내용')
    start = start + len('모집요강 직무내용') if start >= 0 else 0
    stop = t.find('더보기 접기', start)
    stop = stop if stop > 0 else start
    body = t[start:stop]
    contact = t.find('채용 담당자 채용 담당자 정보', stop)  # 담당자 영역은 통째로 버린다
    rest = ' ' + t[stop:contact if contact > stop else len(t)]
    f = fields_in(rest, LABELS)
    comp_start = t.find('기업정보 더보기')
    c = fields_in(' ' + t[comp_start:] if comp_start >= 0 else '', COMPANY_LABELS)
    ben_text = f.get('복리후생', '')
    # 표에서 제공 항목은 이름만, 미제공 항목은 '이름 미제공'으로 적힌다
    benefits = {}
    for b in BENEFITS:
        i = ben_text.find(b)
        benefits[b] = i >= 0 and not ben_text[i + len(b):].lstrip().startswith('미제공')
    reg = re.search(r'채용공고 등록일시 ([\d.]+ [\d:]+)', t)
    rec = dict(
        body=scrub(body)[:6000],
        occupation=f.get('모집 직종', '')[:120], related_occupation=f.get('관련 직종', '')[:120],
        career=f.get('경력', '')[:60], education=f.get('학력', '')[:60],
        employment_type=f.get('고용 형태', '')[:80], wage=f.get('임금 조건', '')[:80],
        work_hours=scrub(f.get('상세 근무시간', ''))[:400], work_form=f.get('근무 형태', '')[:120],
        workplace=scrub(f.get('근무 예정지', '').replace('지도 보기 길찾기', ''))[:150],
        near_station=f.get('인근 전철역', '')[:80], bus_routes=f.get('버스 노선 번호', '')[:150],
        pref_condition=f.get('우대조건', '')[:120], pref_other=scrub(f.get('기타 우대사항', '').split(' 고용허가제')[0])[:600],
        other_wish=scrub(f.get('그 밖의 희망사항', ''))[:600],
        benefit_other=scrub(f.get('기타 복리후생', ''))[:400], benefits=benefits,
        industry=c.get('업종', '')[:80], company_size=c.get('기업규모', '')[:40], employees=c.get('근로자수', '')[:40],
        registered=reg.group(1) if reg else '',
    )
    rec['flag_pref_car'] = '차량소지자' in rec['pref_condition']
    xy = re.search(r'data-map-coor-x="([\d.]+)"\s*data-map-coor-y="([\d.]+)"', page)  # 근무 예정지 지도 좌표(경위도)
    rec['lon'], rec['lat'] = (float(xy.group(1)), float(xy.group(2))) if xy else (None, None)
    return rec


def main():
    with open(DATA / 'list.csv', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    n_ok = n_missing = 0
    with open(DATA / 'postings.jsonl', 'w', encoding='utf-8') as out:
        for r in rows:
            p = DATA / 'detail_html' / f"{r['wanted_auth_no']}.html.gz"
            if not p.exists():
                n_missing += 1
                continue
            with gzip.open(p, 'rt', encoding='utf-8') as g:
                rec = parse(g.read())
            gu = re.search(r'화성시 (\S+구)', r['location'] + ' ' + rec['workplace'])
            rec.update(id=r['wanted_auth_no'], info_type=r['info_type'], company=r['company'], title=r['title'],
                       list_location=r['location'],
                       gu=gu.group(1) if gu else '', close_date=r['close_date'])
            out.write(json.dumps(rec, ensure_ascii=False) + '\n')
            n_ok += 1
    print(f'parsed {n_ok}, missing detail {n_missing}')


if __name__ == '__main__':
    main()
