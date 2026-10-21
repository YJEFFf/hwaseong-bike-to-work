# 데이터 출처와 라이선스 (Data sources & licences)

**2026-09-28 공개 전 점검:** 현재 저장소는 PRIVATE이다. `outputs/private/auto_vector/`
10개 파일은 팀 내부 전달용으로 이미 추적 중이다. `.gitignore`만으로 기존 커밋은
숨겨지지 않는다. 공식 GIS에서 유래한 좌표 CSV와 이 파일들에 대한 재배포 근거를
확인하거나 공개본에서 이력까지 제외하기 전에는 저장소를 그대로 공개하지 않는다.
이번 점검은 공개 전환이나 이력 삭제를 수행하지 않았다.

이 문서는 이 저장소의 산출물(`outputs/`)이 어떤 원자료로부터 파생되었는지,
각 원자료의 라이선스와 필요한 표기 의무가 무엇인지 정리한다. 원자료 자체
(`data/raw/`)는 재배포 허가 미확인 또는 용량 문제로 git에 커밋하지 않으며,
해시·출처·쿼리는 [`data_manifest.json`](data_manifest.json)에 기록되어 있다
(I2). 이 문서는 그 매니페스트를 사람이 읽기 쉽게 요약한 것이다.

**바인딩 규칙**: 이 저장소가 공개되면(레저 결정 32) 아래 표기 의무를 반드시
지킨다. 특히 OSM ODbL의 share-alike 성격의 조항 때문에, OSM 파생 데이터가
섞인 산출물(`outputs/destinations_5186.csv`)은 같은 라이선스(ODbL) 하에
공개해야 할 수 있다 — 배포 전 사람이 확인할 것.

---

## 1. OpenStreetMap (OSM) — ODbL 1.0

- **적용 대상**: `data/raw/osm_hwaseong_bike_20260914.json` (자전거 시설),
  `data/raw/osm_roads/roads.json` (저위계 도로망), `data/raw/osm_poi/stops.json`
  (버스정류장·역). 이 중 버스정류장·역은 `outputs/destinations_5186.csv`에
  이름과 좌표(2,086개, 학교 포함)로 그대로 들어간다.
- **라이선스**: Open Database License (ODbL) 1.0. 원문:
  <https://opendatacommons.org/licenses/odbl/1-0/>
- **필수 표기(attribution)**: "© OpenStreetMap contributors"
- **파생 데이터베이스(share-alike) 주의**: ODbL은 "produced work"(지도 이미지 등)와
  "derivative database"를 구분한다. `outputs/destinations_5186.csv`는 OSM
  요소(버스정류장/역)를 좌표·이름과 함께 그대로 담고 있으므로 **파생
  데이터베이스**로 취급해야 한다 — 즉 이 CSV를 재배포하려면 ODbL 4.4조에 따라
  같은 라이선스(ODbL) 또는 호환 라이선스로 공개해야 할 가능성이 높다. 공모전
  제출 시 이 CSV를 배포한다면 위 attribution과 함께 "이 파일은 OpenStreetMap
  데이터에서 파생된 데이터베이스로 ODbL 1.0의 적용을 받을 수 있음"을 명시할 것.
- **재현 쿼리**: `data_manifest.json`의 `raw_inputs[].query` 참고. `osm_roads/roads.json`,
  `osm_poi/stops.json`의 쿼리는 원본 실행 스크립트가 보존되지 않아 다운로드
  기록으로부터 재구성(reconstructed)한 것이다.

## 2. GHSL (Global Human Settlement Layer) — CC BY 4.0

- **적용 대상**: `data/raw/ghsl/GHS_POP_E2025_GLOBE_R2023A_54009_100_V1_0_R5_C29.tif`,
  `..._R5_C30.tif`. 인구를 화소 단위로 대푯점화(dasymetric)해
  `outputs/origins_5186.csv`(출발지, 인구 가중치)를 만드는 데 쓰인다.
- **라이선스**: Creative Commons Attribution 4.0 International (CC BY 4.0).
  원문: <https://creativecommons.org/licenses/by/4.0/>
- **필수 표기(attribution)**: Schiavina, M., Freire, S., Carioli, A., MacManus, K.
  (2023): *GHS-POP R2023A - GHS population grid multitemporal (1975-2030)*.
  European Commission, Joint Research Centre (JRC).
  DOI: [10.2905/2FF68A52-5B5B-4A22-8F40-C41DA8332CFE](https://doi.org/10.2905/2FF68A52-5B5B-4A22-8F40-C41DA8332CFE)
- **주의**: `outputs/origins_5186.csv`는 GHSL 화소값을 행정안전부 인구 총량에
  맞춰 재조정(dasymetric apportionment, `admdongkor` 경계 사용)한 파생값이다 —
  원본 GHSL 화소값 그대로가 아니다. CC BY는 파생물에도 attribution을 요구한다.

## 3. vuski/admdongkor — 행정동 경계

- **적용 대상**: `data/raw/admin/hwaseong_hjd_20260701.geojson` (전국 파일 중
  화성시만 추출한 부분집합). `analysis.admin`을 통해 인구 재조정(§2)과
  구역 판정(구/읍면동 정의) 전반에 쓰인다.
- **라이선스**: 2026-09-28에 확인한
  [LICENSE-DATA](https://github.com/vuski/admdongkor/blob/master/LICENSE-DATA)는
  경계 데이터에 **CC BY 4.0**를 명시한다. 원 SGIS 출처 표기도 유지한다.
  코드의 MIT와 데이터 라이선스를 구분한다. 기존 ‘라이선스 없음’ 표기를 정정했다.
- **표기**: "행정동 경계: SGIS, vuski/admdongkor (ver20260701), CC BY 4.0".
  화성시 부분 추출·좌표 변환 등 변경 사실도 표시한다.

## 4. 행정안전부(MOIS) — 주민등록 인구

- **적용 대상**: `data/raw/population/pop_raw.bin` (지역별(행정동) 성별
  연령별 주민등록 인구수, 기준 2026-08-31). `outputs/origins_5186.csv`의
  인구 총량 기준(§2의 재조정 대상)으로 쓰인다.
- **출처**: 공공데이터포털 dataset 15097972,
  <https://www.data.go.kr/data/15097972/fileData.do>
- **라이선스**: 2026-09-28 포털 확인: **이용허락범위 제한 없음**.
- **표기**: "인구 통계: 행정안전부 주민등록인구(2026-08-31 기준)".
  GHSL·행정경계를 결합한 파생물에는 각 자료의 조건도 함께 적용한다.

## 5. 공공데이터포털 표준데이터 — 전국초중등학교위치표준데이터

- **적용 대상**: `data/raw/schools/page_1.json`, `page_2.json` (dataset 15021148).
  화성시 소재 학교 192개가 `outputs/destinations_5186.csv`에 이름·좌표로
  들어간다.
- **출처**: <https://www.data.go.kr/data/15021148/standard.do>,
  API: `https://api.data.go.kr/openapi/tn_pubr_public_elesch_mskul_lc_api`
- **라이선스**: 공공데이터(표준데이터). **개별 이용조건 표기 미확인 —
  포털에서 재확인할 것**. 주민등록인구의 조건을 이 자료에 대신 적용하지 않는다.
- **표기(잠정)**: "학교 위치: 공공데이터포털 전국초중등학교위치표준데이터"

## 6. 화성시 GIS (자전거 노선·도로면) — 공개 포털 자료

- **적용 대상**: `data/raw/화성_자전거노선_5186.geojson`,
  `data/raw/화성_자전거도로면_5186.geojson` (WFS `RD.RDL_BYST_LS` /
  `RD.RDL_BYCP_AS`). 이 저장소의 핵심 자전거망 분석 전부의 기반이며,
  `data/raw/yejun08_candidates.csv`(연결 후보)도 같은 원본으로부터 파생됐다.
- **이용**: 화성시 생활공간 웹포털이 누구나 열람할 수 있게 공개하는 WFS 자료다.
  팀 판단(2026-09-15)으로 별도 문의 없이 분석·보고서·그림에 사용한다. 출처는
  "화성시 생활공간 웹포털"로 표기한다. 참고: 화성시 저작권정책
  <https://www.hscity.go.kr/agree/copyright_policy.jsp>
- **처리 방침**: `.gitignore`가 `data/raw/`를 통째로 git에서 제외한다. 이번 점검에서
  WFS 원본의 커밋은 발견하지 않았다. 다만 이로부터 계산한 **집계값**(`access_by_dong.csv`,
  `region_selection.csv`, `priority_summary.csv` 등)은 원 지오메트리를 복원할
  수 없는 형태이므로 공개 가능하다고 판단했다(레저 기록 참고). 원본 좌표가
  드러나는 산출물은 별도 점검 대상이다. 기존 설명과 달리 `outputs/private/auto_vector/`
  의 10개 파일은 PRIVATE 팀 전달을 위해 추적 중이다. `outputs/auto_vector/targets_171.csv`
  와 `commute/results/gap_points_50m.csv`, `gap_priority_commute.csv`, `gap_priority_greedy.csv`
  에도 좌표가 있다. 개별 재배포 허락을 확인하거나 공개본에서 제외해야 한다.
- 포털 파일자료 [15118266](https://www.data.go.kr/data/15118266/fileData.do)의
  ‘이용허락범위 제한 없음’은 그 첨부 ZIP의 조건이다. 별개 WFS 노선에도 같은
  조건이 적용된다고 추정하지 않는다.

## 7. 공장등록 원자료와 브이월드 지오코딩

- 공장등록 원자료에는 연락처 열이 있으므로 원본과 지오코딩 캐시·수동 보정 좌표를
  **이 제출레포에는 올리지 않는다.** 집계와 원자료가 재구성되지 않는 결과만 선별한다.
  09-28 팀 공유 지시에 따라 PRIVATE 작업 레포에 입력 3개가 추가됐다. 전체 재계산은
  그 경로에서 읽기만 했으며 제출레포에는 복사하지 않았다. 작업 레포의 비공개 공유와
  서비스 이용조건 충족 여부는 별개이며, 작업 레포도 그대로 공개하지 않는다.
- [브이월드 이용약관](https://www.vworld.kr/v4po_prcint_a001.do)의 현재 관련 조항은
  **제19조(저작권)**다(확인일 2026-09-28). 이전 문서의 제12조 표기는 정정한다.
  정보의 복제·제공 및 무단 저장 제한을 확인해야 하며, Git 밖 로컬 저장 자체가
  이용 허가를 뜻하지 않는다. 이번 검수에서 새 지오코딩을 요청하지 않았다.
- 공개 망에서 뽑은 좌표도 §6의 재배포 조건과 별도로 대조해야 한다.

---

## 요약 표

| 데이터 | 라이선스 | 저장소 커밋 | 표기 의무 |
|---|---|---|---|
| OSM (자전거/도로/POI) | ODbL 1.0 | 원본 미커밋(`data/raw/` gitignore) | © OpenStreetMap contributors; `destinations_5186.csv`는 파생 DB로 share-alike 검토 필요 |
| GHSL GHS-POP | CC BY 4.0 | 원본 미커밋 | Schiavina et al. 2023 인용 |
| vuski/admdongkor | CC BY 4.0 (LICENSE-DATA 확인) | 원본 미커밋 | SGIS·vuski 출처, 변경 사실 표시 |
| MOIS 주민등록인구 | 이용허락범위 제한 없음 | 원본 미커밋 | 행정안전부 출처 표기 |
| 표준데이터(학교) | 공공데이터(유형 미확인) | 원본 미커밋 | 공공데이터포털 출처 표기 |
| 화성시 GIS(자전거 노선/도로면) | 재배포 허가 미확인 | 원본 미커밋, 파생 좌표·private 전달물은 일부 추적 중 | 출처 표시만으로 공개 허용을 단정하지 않음 |
| 공장등록·브이월드 좌표 | 연락처 및 브이월드 이용조건 적용 | 원본·캐시·수동 좌표 미커밋 | 공개 제외; 저장 조건도 별도 확인 |

---

## English summary (short)

This repository derives its outputs from several third-party datasets, none of
which are committed to git (`data/raw/` is gitignored); hashes, sources, and
retrieval details are in [`data_manifest.json`](data_manifest.json).

- **OpenStreetMap** (bicycle facilities, roads, bus stops/stations) — **ODbL 1.0**.
  Attribution: "© OpenStreetMap contributors". `outputs/destinations_5186.csv`
  embeds OSM-derived points (names + coordinates) and should be treated as a
  derivative database subject to ODbL's share-alike clause — confirm before
  redistribution.
- **GHSL GHS-POP** (population raster, tiles R5_C29/R5_C30) — **CC BY 4.0**.
  Cite: Schiavina, Freire, Carioli, MacManus (2023), GHS-POP R2023A, JRC,
  DOI 10.2905/2FF68A52-5B5B-4A22-8F40-C41DA8332CFE. `outputs/origins_5186.csv`
  is a dasymetric derivative of this raster.
- **vuski/admdongkor** — **CC BY 4.0** under the current LICENSE-DATA;
  retain both upstream SGIS and vuski attribution and disclose modifications.
- **MOIS population** — unrestricted use on its portal listing as checked on
  2026-09-28. **School locations** remain pending verification of their specific terms.
- **Hwaseong city GIS** (bicycle route/surface WFS, and the derived candidate
  list) — publicly viewable portal data, used for analysis, report and figures
  by team decision (2026-09-15) with source attribution; raw files are not
  committed. However, private handoff files and derived coordinates ARE tracked,
  including ten files under `outputs/private/auto_vector/`. Do not make this
  repository public before clearing or excluding them and their history.
- **Factory originals and VWorld-derived coordinates** are excluded from this
  submission repository. The separate PRIVATE work repository holds the inputs
  shared for this rerun and must not be made public with those files or their history.
  The relevant current VWorld provision is Article 19; local storage does not
  itself establish permission under the service terms.
