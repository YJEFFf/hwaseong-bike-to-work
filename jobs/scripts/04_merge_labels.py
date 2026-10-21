"""AI 분류 1차(A)·2차(B) 결과 합치기: 형식 점검, 근거 문장 원문 대조, 일치도, 불일치 목록, 최종 라벨.

- 근거(evidence)는 ' / '로 나눈 조각마다 입력 텍스트에 그대로 있어야 한다. 없으면 '근거 불일치'로 표시한다.
- 1차·2차가 같은 항목은 그대로 쓰고, 다른 항목은 adjudication.json(원문을 다시 읽고 정한 값)을 쓴다.
- 일치도는 단순 일치율과 Cohen's kappa를 함께 낸다.
- 최종 판정 전수 검수(prompt_audit.md)에서 확인된 수정은 audit_fixes.json으로 덮어쓰고, 고친 건수만 ai_agreement.csv에 남긴다.
- 마지막으로 Claude가 원문 전체를 한 건씩 읽은 판정(claude_review.jsonl, 기준 prompt_review.md)을 최종값으로 쓴다.
  직무에 운전·근무지 밖 이동이 있는 공고는 job_travel=True와 원문 근거 문구(job_travel_evidence)를 붙인다.
"""
import json, os, re
from collections import Counter
from pathlib import Path

DATA = Path(os.environ.get('JOBS_DATA', 'jobs_private'))
OUT = Path(os.environ.get('JOBS_OUT', Path(__file__).resolve().parents[1] / 'results'))
FIELDS = ['car_level', 'car_purpose', 'transit_barrier', 'shuttle', 'night_or_shift', 'occupation_group']
ALLOWED = dict(car_level={'필수', '우대', '없음'}, car_purpose={'출퇴근', '업무', '불명', '해당없음'},
               transit_barrier={'예', '아니오'}, shuttle={'예', '아니오'}, night_or_shift={'예', '아니오'},
               occupation_group={'생산·제조', '운전·운송·배송', '영업·판매', '사무·관리', '돌봄·보건·복지',
                                 '건설·설치·정비', '음식·서비스', '기타'})


def norm(s):
    return re.sub(r'\s+', ' ', s or '').strip()


def load_pass(name):
    out = {}
    for p in sorted((DATA / 'labels' / name).glob('batch_*.json')):
        for rec in json.loads(p.read_text(encoding='utf-8')):
            out[rec['id']] = rec
    return out


def evidence_ok(rec, src):
    ev = norm(rec.get('evidence', ''))
    if rec.get('car_level') == '없음':
        return True
    if not ev:
        return False
    text = norm(' '.join(str(src.get(k, '')) for k in ('title', 'body', 'pref_other', 'other_wish', 'benefit_other', 'work_hours')))
    for piece in [p.strip() for p in ev.split(' / ') if p.strip()]:
        if piece == '[우대조건: 차량소지자]':
            if '차량소지자' not in src.get('pref_condition', ''):
                return False
        elif piece.strip('"“”\'') not in text:
            return False
    return True


def kappa(a, b):
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / n / n
    return po, (po - pe) / (1 - pe) if pe < 1 else 1.0


def main():
    src = {}
    for p in sorted((DATA / 'batches').glob('batch_*.json')):
        for r in json.loads(p.read_text(encoding='utf-8')):
            src[r['id']] = r
    A, B = load_pass('passA'), load_pass('passB')
    problems = []
    for name, P in (('A', A), ('B', B)):
        missing = set(src) - set(P)
        if missing:
            problems.append(f'{name}: 누락 {len(missing)}건')
        for i, rec in P.items():
            for f in FIELDS:
                if rec.get(f) not in ALLOWED[f]:
                    problems.append(f'{name} {i}: {f}={rec.get(f)!r}')
    ids = [i for i in src if i in A and i in B]
    rows, agree = [], {}
    for f in FIELDS:
        agree[f] = kappa([A[i][f] for i in ids], [B[i][f] for i in ids])
    ev_bad = {n: sum(not evidence_ok(P[i], src[i]) for i in ids) for n, P in (('A', A), ('B', B))}
    adj_path = DATA / 'adjudication.json'
    adj = json.loads(adj_path.read_text(encoding='utf-8')) if adj_path.exists() else {}
    todo, final = [], []
    for i in ids:
        diff = [f for f in FIELDS[:5] if A[i][f] != B[i][f]]
        if not evidence_ok(A[i], src[i]):
            diff.append('evidence_A')
        rec = {f: A[i][f] for f in FIELDS} | dict(id=i, evidence=A[i].get('evidence', ''), source='A=B')
        if diff:
            if i in adj:
                rec.update({k: v for k, v in adj[i].items() if k in FIELDS or k == 'evidence'}, source='재판정')
            else:
                todo.append(dict(id=i, diff=diff, A=A[i], B=B[i], input=src[i]))
                rec['source'] = '미판정'
        final.append(rec)
    # 전수 검수(prompt_audit.md)에서 확인된 수정: audit_fixes.json = {공고번호: {항목: 값, ..., "reason": ...}}
    fix_path = DATA / 'audit_fixes.json'
    fixes = json.loads(fix_path.read_text(encoding='utf-8')) if fix_path.exists() else {}
    n_fixed, fixed_fields = 0, Counter()
    for rec in final:
        fx = fixes.get(rec['id'])
        if fx:
            fixed_fields.update(k for k in fx if k in FIELDS and fx[k] != rec[k])
            rec.update({k: v for k, v in fx.items() if k in FIELDS or k == 'evidence'})
            if rec['car_level'] == '없음':
                rec['car_purpose'], rec['evidence'] = '해당없음', ''
            rec['source'] += '+검수수정'
            n_fixed += 1
    print('audit fixes applied', n_fixed)
    # Claude 전수 확인(prompt_review.md 기준으로 원문 전체를 한 건씩 읽은 판정)을 최종값으로 쓴다.
    # job_travel: 직무에 차 운전(배송·납품·운송·수행·송영)이나 근무지 밖 이동(방문·외근 영업·출장·현장 설치)이 있는 공고.
    # 회사 차량을 쓰든 개인 차량을 쓰든, 자차 조건 이유가 무엇이든 05에서 본 지표에서 뺀다. 근거 문구(wt_ev)는 원문 대조한다.
    rev_path = DATA / 'claude_review.jsonl'
    rev = {}
    if rev_path.exists():
        for line in open(rev_path, encoding='utf-8'):
            r = json.loads(line)
            rev[r['id']] = r
    MAP = dict(lv='car_level', pu='car_purpose', tb='transit_barrier', sh='shuttle', ns='night_or_shift')
    n_rev, rev_fields = 0, Counter()
    for rec in final:
        r = rev.get(rec['id'])
        rec['job_travel'], rec['job_travel_evidence'] = False, ''
        if not r:
            continue
        new = {MAP[k]: r[k] for k in MAP}
        new['car_purpose'] = {'-': '해당없음'}.get(new['car_purpose'], new['car_purpose'])
        changed = [f for f, v in new.items() if rec[f] != v]
        rev_fields.update(changed)
        rec.update(new)
        rec['evidence'] = r['ev'] if new['car_level'] != '없음' else ''
        if new['car_level'] != '없음' and r['wt'] == '예':
            rec['job_travel'], rec['job_travel_evidence'] = True, r.get('wt_ev', '')
        if changed:
            rec['source'] += '+전수확인수정'
            n_rev += 1
    ev_final_bad = [rec['id'] for rec in final if not evidence_ok(rec, src[rec['id']])]
    jt_bad = [rec['id'] for rec in final if rec['job_travel'] and not evidence_ok(
        dict(car_level='필수', evidence=rec['job_travel_evidence']), src[rec['id']])]
    print('claude review applied', len(rev), 'changed postings', n_rev, dict(rev_fields),
          'final evidence bad', len(ev_final_bad), ev_final_bad[:20],
          'job travel', sum(r['job_travel'] for r in final), 'job travel evidence bad', jt_bad)
    (DATA / 'adjudication_todo.json').write_text(json.dumps(todo, ensure_ascii=False, indent=1), encoding='utf-8')
    with open(DATA / 'labels_final.jsonl', 'w', encoding='utf-8') as f:
        for r in final:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / 'ai_agreement.csv', 'w', encoding='utf-8-sig') as f:
        f.write('field,n,agreement,kappa\n')
        for k, (po, kp) in agree.items():
            f.write(f'{k},{len(ids)},{po:.4f},{kp:.4f}\n')
        f.write(f'evidence_not_in_text_A,{len(ids)},{ev_bad["A"]},\nevidence_not_in_text_B,{len(ids)},{ev_bad["B"]},\n')
        f.write(f'audit_fixed_postings,{len(final)},{n_fixed},\n')
        for k in FIELDS:
            if fixed_fields[k]:
                f.write(f'audit_fixed_{k},{len(final)},{fixed_fields[k]},\n')
        f.write(f'review_checked_postings,{len(final)},{len(rev)},\n')
        f.write(f'review_changed_postings,{len(final)},{n_rev},\n')
        for k in FIELDS:
            if rev_fields[k]:
                f.write(f'review_changed_{k},{len(final)},{rev_fields[k]},\n')
        f.write(f'review_job_travel,{len(final)},{sum(r["job_travel"] for r in final)},\n')
        f.write(f'final_evidence_not_in_text,{len(final)},{len(ev_final_bad)},\n')
        f.write(f'job_travel_evidence_not_in_text,{len(final)},{len(jt_bad)},\n')
    print('problems', len(problems), problems[:10])
    print('agreement', {k: (round(a, 3), round(b, 3)) for k, (a, b) in agree.items()}, 'evidence bad', ev_bad)
    print('to adjudicate', len(todo), 'final', len(final), Counter(r['source'] for r in final))


if __name__ == '__main__':
    main()
