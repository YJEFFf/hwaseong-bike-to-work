"""사람 검증 시트(verify_sheet_100.csv)를 읽기 쉬운 HTML로 만든다. 비공개 폴더에만 쓴다(원문 포함).

검증자는 원문을 읽고 AI 판정이 맞는지 표시한다. 결과는 같은 CSV의 human_* 칸에 적어 04·05를 다시 돌리면 된다.
"""
import csv, html, os
from pathlib import Path

DATA = Path(os.environ.get('JOBS_DATA', 'jobs_private'))
rows = list(csv.DictReader(open(DATA / 'verify_sheet_100.csv', encoding='utf-8-sig')))
parts = ['<!doctype html><meta charset="utf-8"><title>채용공고 AI 판정 검증 100건</title>',
         '<style>body{font-family:"Malgun Gothic",sans-serif;max-width:900px;margin:24px auto;padding:0 16px;line-height:1.55;color:#222;background:#fff}'
         '.c{border:1px solid #ccc;border-radius:6px;padding:12px 14px;margin:14px 0}.m{color:#555;font-size:13px}'
         '.ai{background:#f3f6fa;padding:6px 8px;border-radius:4px;margin-top:8px}mark{background:#ffe58a}</style>',
         '<h1>채용공고 AI 판정 검증 (무작위 100건)</h1><p class="m">원문을 읽고 최종 판정의 자차 조건(필수·우대·없음)과 이유(출퇴근·업무·불명)가 맞는지 확인한다. '
         '업무(추정)는 직무가 근무지 밖 이동(방문·납품·영업·설치·출장)이고 회사 차량 언급이 없는 공고로, 보고서 본 지표에서는 업무로 뺀다. '
         '판정 기준은 jobs/prompt_review.md. 틀리면 verify_sheet_100.csv의 human_car_level·human_car_purpose 칸에 맞는 값을, 맞으면 비워 둔다.</p>']
for k, r in enumerate(rows, 1):
    text = html.escape(r['text'])
    for kw in ('자차', '자가용', '차량', '출퇴근', '통근', '대중교통', '운전'):
        text = text.replace(kw, f'<mark>{kw}</mark>')
    parts.append(f'<div class="c"><b>{k}. {html.escape(r["title"])}</b><div class="m">{html.escape(r["id"])} · {html.escape(r["occupation"])} · '
                 f'우대조건 칸: {html.escape(r["pref_condition"])}</div><p>{text}</p>'
                 f'<div class="ai">AI 판정: <b>{html.escape(r["ai_car_level"])}</b> / <b>{html.escape(r["ai_car_purpose"])}</b><br>'
                 f'근거: {html.escape(r["ai_evidence"])}</div></div>')
(DATA / 'verify_sheet_100.html').write_text('\n'.join(parts), encoding='utf-8')
print('ok', len(rows))
