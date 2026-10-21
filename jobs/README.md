# 채용공고로 본 자차 출퇴근 조건 (2026-10-22 추가 분석)

화성시 채용공고 원문을 AI로 읽어 "차가 없는 구직자가 불리한 조건"(자차 필수·우대와 그 이유)을 정리하고,
근무지에서 자전거망·버스 정류소까지의 거리, 구별 자전거 접근성(`commute/`)과 겹쳐 본다.

## 자료

- 고용24 채용정보 상세검색, 근무지역 화성시(41590), 2026-10-22 00시 게시 중 공고. 목록 5,512건 중
  고용24에 직접 등록된 공고(구인인증, `infoTypeCd=VALIDATION`) 3,475건을 분석한다. 민간 포털 연계 공고는
  고용24 상세 화면에 본문·우대조건·구가 없어 제외했다. robots.txt는 `/wk/` 경로를 허용한다. 요청 간격은 작업자당 1초다.
- 근무 예정지 좌표: 공고 상세 화면 지도 버튼의 경위도(`data-map-coor-x/y`).
- 경기버스정보(GBIS) 정류소·노선 경유 파일(2026-10-21 내려받음). 노선이 경유하는 정류소만 쓴다.
- 자전거망: `commute/scripts/00_restore_osm_nodes.py`로 노드 번호를 복원한 합집합망(공식 GIS + OSM).
- 비교 도시 수치(`results/region_compare_20261022.csv`): 고용24 검색 건수(정보제공처=고용24, 우대조건=차량소지자).

## 개인정보와 공개 범위

공고 원문에는 채용 담당자 이름·전화·이메일이 있다. `02_parse.py`는 담당자 영역을 읽지 않고, 본문의 전화번호·이메일은
지운 뒤에만 AI에 넣는다. 원문·좌표는 `JOBS_DATA`(비공개 폴더)에만 두고, 이 폴더의 `results/`에는 집계만 올린다.

## 실행 (저장소 루트, `JOBS_DATA`=비공개 폴더)

```
python -X utf8 jobs/scripts/01_collect.py all      # 목록 → 상세 원문(gzip)
python -X utf8 jobs/scripts/02_parse.py            # 항목 추출, 담당자 정보 제외
python -X utf8 jobs/scripts/03_prepare.py          # AI 분류 대상 선정·배치, 규칙 기반 기준 분류
#   AI 분류: prompt_classify.md로 1차(A, Claude Opus)·2차(B, Claude Sonnet)를 서로 모르게 따로 실행
#   → JOBS_DATA/labels/passA, passB/batch_*.json
python -X utf8 jobs/scripts/04_merge_labels.py     # 형식·근거 문장 대조, 일치도, 불일치 목록
#   불일치는 prompt_adjudicate.md로 재판정 → JOBS_DATA/adjudication.json, 다시 04 실행
#   최종 판정 775건 전수 검수: prompt_audit.md(Claude Opus, 20묶음) → 규칙에 어긋난 것만
#   JOBS_DATA/audit_fixes.json에 적고 다시 04 실행(고친 건수는 results/ai_agreement.csv 끝줄)
JOBS_NET_ROOT=<복원 망 작업 폴더> JOBS_GBIS=<GBIS CSV 폴더> JOBS_COMMUTE_RESULTS=commute/results \
python -X utf8 jobs/scripts/05_analyze.py          # 구·직종·거리별 집계, 검증용 무작위 100건 시트
python -X utf8 jobs/scripts/06_figure.py           # 그림(보고서 그림 3)
python -X utf8 jobs/scripts/07_verify_sheet_html.py # 무작위 100건 시트를 원문과 나란히 보는 HTML(비공개 폴더)
```

AI 분류는 같은 입력에도 결과가 조금씩 달라질 수 있다. 그래서 두 모델의 결과를 비교하고, 근거 문장이 원문에 실제로
있는지 기계적으로 확인하며, 사람이 원문과 대조할 무작위 100건 시트(`verify_sheet_100.csv`)를 만든다.

2026-10-22 전수 검수 결과: 775건 중 검수자가 '틀림' 21건, '애매' 78건을 냈다. 틀림 21건 가운데 17건(강도 표현 없는
'필수' → '우대')은 받아들였고, 4건('인근 거주자, 차량소지자'를 출퇴근으로 보자는 것)은 받아들이지 않았다. 이유를 적지 않은
나열은 1·2차가 일치한 공고에서 모두 '불명'으로 판정돼 있어, 재판정 규칙 2를 그쪽에 맞춰 고쳤다. 애매 판정과 무작위 100건을
다시 읽으며 같은 기준인데 값이 엇갈린 7건(대중교통을 직접 쓰지 않은 '대중교통 불편' 3건 포함)을 통일했다. 모두 24건,
자차 조건 18·이유 3·대중교통 불편 3개 항목이 바뀌었다. 자차 필수·우대 공고 수(617)와 업무용을 뺀 공고 수(602)는
그대로이고, 출퇴근 이유 공고는 158 → 157건, 대중교통 불편은 28 → 25건이 됐다. 검수는 AI가 했고 사람이 전수 확인하지는 않았다.

업무용 운전의 범위가 좁다는 지적에 대비해, 제목·본문·직종에 설치 방문·납품·출장·현장 이동·영업·배송·A/S·거래처가 있는 공고를
모두 업무용으로 보는 넓은 기준도 같이 낸다(`jobs_method_summary.json`의 `broad_work_sensitivity`). 이때도 서부 15.5%,
동부 5.7%, 같은 직종끼리 승산비 2.40(1.68~3.43)이다.
