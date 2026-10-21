"""AI 분류 1차(A)·2차(B) 결과 합치기: 형식 점검, 근거 문장 원문 대조, 일치도, 불일치 목록, 최종 라벨.

- 근거(evidence)는 ' / '로 나눈 조각마다 입력 텍스트에 그대로 있어야 한다. 없으면 '근거 불일치'로 표시한다.
- 1차·2차가 같은 항목은 그대로 쓰고, 다른 항목은 adjudication.json(원문을 다시 읽고 정한 값)을 쓴다.
- 일치도는 단순 일치율과 Cohen's kappa를 함께 낸다.
- 최종 판정 전수 검수(prompt_audit.md)에서 확인된 수정은 audit_fixes.json으로 덮어쓰고, 고친 건수만 ai_agreement.csv에 남긴다.
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
    text = norm(' '.join(str(src.get(k, '')) for k in ('title', 'body', 'pref_other', 'benefit_other', 'work_hours')))
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
    print('problems', len(problems), problems[:10])
    print('agreement', {k: (round(a, 3), round(b, 3)) for k, (a, b) in agree.items()}, 'evidence bad', ev_bad)
    print('to adjudicate', len(todo), 'final', len(final), Counter(r['source'] for r in final))


if __name__ == '__main__':
    main()
