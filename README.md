# 일터까지 이어지는 자전거길 — 화성시 끊긴 자전거망과 '자차 출퇴근' 채용공고

한겨레 × 숲과나눔 「AI와 함께하는 교통문제 해결을 위한 데이터 분석 공모전」(2026) 팀 북적북적의 분석 코드와 집계 결과입니다.

- `commute/`: 공식 GIS 자전거도로와 OpenStreetMap 자전거 경로를 합친 망에서 끊긴 지점을 찾고, 근로 연령 주민이 자전거 15분 안에 공장 일자리에 닿는 몫과 연결 우선순위·조건부 탄소 감축량을 계산합니다.
- `jobs/`: 고용24 화성시 채용공고 원문을 AI로 읽어 자차 조건(필수·우대·없음)과 이유(출퇴근·업무·불명)를 분류하고, 근무지에서 자전거망·버스 정류소까지 거리와 겹쳐 봅니다.
- `analysis/`, `scripts/`: 두 분석이 공통으로 쓰는 망 구성·경로 계산 코드입니다.

## 주요 결과 (보고서 기준)

| 항목 | 값 | 파일 |
|---|---|---|
| 자전거망 연결 덩어리 / 20m 안 끊긴 지점 / 50m 안 | 208 / 212 / 324 | `commute/results/gaps_summary.csv` |
| 직선 15분 안 공장 일자리 중 자전거망으로 닿는 몫 | 7.3% (최대 집계점 제외 11.0%) | `commute/results/commute_access.csv`, `sensitivity_round2.csv` |
| 끊긴 틈 + 공장 진입 구간 연결 시 | 21.8% (최대 집계점 제외 17.7%) | `commute/results/sensitivity_round2.csv` |
| 채용공고 AI 분류 결과 | `jobs/results/` | `jobs_by_gu.csv`, `jobs_method_summary.json`, `ai_agreement.csv` |

## 재현

Python 3.12 이상, `pip install -r requirements.txt`.

1. 원자료 받기: `data_manifest.json`의 URL·해시대로 `outputs/runs/loo_work/data/raw/`에 내려받습니다(`scripts/00b_fetch_public.py`). 출발지·목적지 격자는 `scripts/02b_origins_destinations.py`로 만듭니다.
2. OSM 노드 번호 복원: `python -X utf8 commute/scripts/00_restore_osm_nodes.py` (2026-09-14 추출본은 도로끼리 공유하는 노드 번호가 빠져 있어 교량·터널이 끊긴 것으로 계산됩니다. 추출 시점의 OSM 공개 이력에서 되살립니다.)
3. 공장 좌표: 화성시 공장등록 현황(2024-02)을 받아 `FACTORY_DIR`에 두고 `commute/scripts/01_geocode_factories.py`(브이월드 키 필요)를 실행합니다.
4. `commute/scripts/` 03→02→04→05→07→06 순서로 실행합니다. 자세한 내용은 `commute/README.md`.
5. 채용공고: `jobs/README.md`의 순서(수집 → 파싱 → AI 분류 → 합치기 → 집계 → 그림)를 따릅니다. AI 분류 지시문은 `jobs/prompt_classify.md`, 재판정·검수·전수 확인 기준은 `prompt_adjudicate.md`, `prompt_audit.md`, `prompt_review.md`입니다.

## 공개하지 않는 것

- 공장등록 원자료(연락처 포함)와 브이월드로 변환한 공장 좌표: 이용약관과 개인정보 보호 때문에 올리지 않습니다.
- 채용공고 원문과 채용 담당자 정보: 원문은 고용24 게시물이고 담당자 연락처가 있어 올리지 않습니다. 공고별 분류 라벨도 올리지 않고 집계만 둡니다.
- 끊긴 지점별 좌표 목록(`gap_points_50m.csv`, `gap_priority_*.csv`): 공식 GIS 파생 좌표의 재배포 조건을 확인하지 못해 뺐습니다. 위 순서대로 실행하면 다시 만들어집니다.

## 자료 출처와 라이선스

© OpenStreetMap contributors (ODbL), GHSL GHS-POP (CC BY 4.0), 화성시 생활공간 웹포털, 화성시 공장등록 현황, 행정안전부 주민등록 인구(공공데이터포털), 통계청 KOSIS, 행정동 경계(vuski/admdongkor), 고용24(한국고용정보원), 경기버스정보(GBIS). 자세한 조건은 `DATA_LICENSES.md`.

AI 도구: Claude Code(Anthropic; Claude Opus·Sonnet), Codex(OpenAI). 채용공고 분류는 두 모델이 따로 분류해 비교했고, 근거 문장을 원문과 대조했으며, Claude가 후보 852건의 원문 전체를 한 건씩 다시 읽어 확정했습니다(`jobs/prompt_review.md`, `jobs/README.md`의 검증 기록). 판정은 AI가 했고 사람이 전수 확인한 것은 아닙니다.
