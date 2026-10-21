"""AI 분류 대상 선정·배치 작성과 규칙 기반 기준 분류.

대상: 우대조건 '차량소지자'이거나, 본문·우대·복리후생에 자차·차량 소지·출퇴근·통근·대중교통 표현이 있는 공고.
AI에는 분류에 필요한 칸만 넣는다(전화·이메일은 02에서 지움, 담당자 정보는 처음부터 없음).
규칙 기준(rule_*)은 AI 없이 정규식만으로 같은 항목을 판정해, AI가 더해 준 것이 무엇인지 비교하는 데 쓴다.
"""
import json, os, random, re
from pathlib import Path

DATA = Path(os.environ.get('JOBS_DATA', 'jobs_private'))
CAR = re.compile(r'자차|자가용|자가\s*차량|개인\s*차량|본인\s*차량|차량\s*(?:소지|소유|보유)|차량소지|차\s*(?:있으신|있는)\s*분')
COMMUTE = re.compile(r'출퇴근|출\s*퇴근|통근|대중교통|교통편|교통이?\s*불편|셔틀')
MUST = re.compile(r'(?:자차|자가용|차량|차)\s*(?:소지|소유|보유)?\s*(?:필수|필요|필히|있어야|있으셔야|소지자만|가능자만)|(?:필수)\s*[:：]?\s*[^■◆]{0,30}(?:자차|자가용|차량\s*소지)')
WORK_DRIVE = re.compile(r'방문|라운딩|배송|배달|영업|출장|납품|외근|현장\s*(?:이동|순회)|순회|운전\s*(?:업무|가능자 필수)')
BATCH = 40


def text_of(r):
    return ' '.join([r['title'], r['body'], r['pref_other'], r['benefit_other']])


def rule_label(r):
    t = text_of(r)
    car = CAR.search(t) or r['flag_pref_car']
    level = '없음' if not car else ('필수' if MUST.search(t) else '우대')
    if level == '없음':
        purpose = '해당없음'
    else:
        near = any(COMMUTE.search(t[max(0, m.start() - 30): m.end() + 30]) for m in CAR.finditer(t))
        purpose = '출퇴근' if near or re.search(r'대중교통\s*(?:이용\s*)?(?:불가|어려|불편|없)', t) else (
            '업무' if WORK_DRIVE.search(t) else '불명')
    return dict(rule_car_level=level, rule_car_purpose=purpose)


def ai_record(r):
    body = r['body']
    if len(body) > 1500:  # 앞부분과 키워드 주변만 남긴다
        keep = [body[:900]]
        for m in list(CAR.finditer(body)) + list(COMMUTE.finditer(body)):
            if m.start() > 900:
                keep.append(body[max(0, m.start() - 150): m.end() + 150])
        body = ' … '.join(keep)[:2500]
    return dict(id=r['id'], title=r['title'], occupation=r['occupation'], industry=r['industry'],
                work_hours=r['work_hours'][:200], work_form=r['work_form'][:80],
                pref_condition=r['pref_condition'], pref_other=r['pref_other'][:400],
                benefit_shuttle='제공' if r['benefits'].get('통근버스') else '미제공',
                benefit_other=r['benefit_other'][:200], body=body)


def main():
    recs = [json.loads(l) for l in open(DATA / 'postings.jsonl', encoding='utf-8')]
    cand = []
    for r in recs:
        t = text_of(r)
        # 고용24 직접 등록 공고(VALIDATION)만 본문·우대조건·구가 있어 분석 대상으로 삼는다
        r['cand'] = r['info_type'] == 'VALIDATION' and bool(r['flag_pref_car'] or CAR.search(t) or COMMUTE.search(t))
        r.update(rule_label(r))
        if r['cand']:
            cand.append(r)
    # 수집 도중에 분류를 시작할 수 있게, 이미 배치에 들어간 공고는 그대로 두고 새 후보만 새 배치로 덧붙인다
    bdir = DATA / 'batches'
    bdir.mkdir(exist_ok=True)
    done = set()
    for p in bdir.glob('batch_*.json'):
        done |= {r['id'] for r in json.loads(p.read_text(encoding='utf-8'))}
    new = sorted((r for r in cand if r['id'] not in done), key=lambda r: r['id'])
    random.Random(20261022).shuffle(new)  # 배치에 구·업종이 몰리지 않게 섞는다(고정 시드)
    start = len(list(bdir.glob('batch_*.json')))
    for k, i in enumerate(range(0, len(new), BATCH)):
        part = [ai_record(r) for r in new[i:i + BATCH]]
        (bdir / f'batch_{start + k:03d}.json').write_text(json.dumps(part, ensure_ascii=False, indent=0), encoding='utf-8')
    with open(DATA / 'postings_flags.jsonl', 'w', encoding='utf-8') as f:
        for r in recs:
            f.write(json.dumps({k: r[k] for k in ('id', 'info_type', 'gu', 'occupation', 'industry', 'flag_pref_car', 'cand',
                                                  'rule_car_level', 'rule_car_purpose')} |
                               {'shuttle_struct': r['benefits'].get('통근버스', False)}, ensure_ascii=False) + '\n')
    print(f'postings {len(recs)}, candidates {len(cand)}, new {len(new)}, batch files {len(list(bdir.glob("batch_*.json")))}')


if __name__ == '__main__':
    main()
