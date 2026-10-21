"""공개(비공식) 원자료 재다운로드 계획/실행 (I2).

사용법:
    python scripts/00b_fetch_public.py --dry-run          # 계획만 출력, 아무 것도 받지 않음
    python scripts/00b_fetch_public.py [--repo-root PATH] [--force]

scripts/00_fetch.py는 화성시 WFS 공식 두 파일(자전거 노선/도로면)만 받는다.
이 스크립트는 그 밖에 파이프라인이 쓰는, "공개" 소스에서 온 원자료를 다룬다
(data_manifest.json의 raw_inputs 항목과 URL/쿼리가 1:1로 대응한다):

  - OSM Overpass 3종(자전거 시설, 저위계 도로망, 버스정류장/역 POI): Overpass API로
    자동 재다운로드할 수 있다.
  - admin/hwaseong_hjd_20260701.geojson (vuski/admdongkor): GitHub raw에서 자동
    다운로드할 수 있지만, 전국 파일이므로 받은 뒤 화성시 부분집합으로 직접 필터링
    해야 한다(이 스크립트는 필터링을 하지 않는다 — 원본만 내려받는다).
  - GHSL 타일 2개: JRC FTP에서 ZIP으로만 배포되어 자동 다운로드 후 압축 해제가
    필요하다. 이 스크립트는 계획에만 표시하고 자동으로 받지 않는다.
  - population/pop_raw.bin, schools/page_*.json: 공공데이터포털 다운로드/API로,
    세션 쿠키·캡차 또는 서비스키가 필요해 안전하게 자동화할 수 없다. 이 스크립트는
    계획에 포털 안내만 표시한다.

이 스크립트는 테스트에서 실행되지 않는다(네트워크 호출을 하므로). `--dry-run`은
계획만 출력하고 아무 것도 내려받지 않는다. 이 저장소의 원본 화성시 WFS(공식) 두
파일은 scripts/00_fetch.py가 담당하므로 여기서 다루지 않는다.
"""
import argparse
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import config

OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"

OSM_BIKE_QUERY = (
    '[out:json][timeout:240];area(3602409173)->.a;('
    'way["highway"="cycleway"](area.a);'
    'way["cycleway"~"lane|track|shared_lane|opposite_lane|separate"](area.a);'
    'way["cycleway:both"~"."](area.a);'
    'way["cycleway:left"~"."](area.a);'
    'way["cycleway:right"~"."](area.a);'
    'way["bicycle"="designated"](area.a););out tags geom;'
)

OSM_ROADS_QUERY = (
    '[out:json][timeout:300];area(3602409173)->.a;way["highway"](area.a);out geom;'
)

OSM_STOPS_QUERY = (
    '[out:json][timeout:300];area(3602409173)->.a;('
    'node["highway"="bus_stop"](area.a);'
    'node["railway"="station"](area.a);'
    'node["public_transport"="station"](area.a););out body;'
)

ADMDONGKOR_URL = (
    "https://raw.githubusercontent.com/vuski/admdongkor/master/ver20260701/"
    "HangJeongDong_ver20260701.geojson"
)

GHSL_TILE_URLS = {
    "GHS_POP_E2025_GLOBE_R2023A_54009_100_V1_0_R5_C29.tif": (
        "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/"
        "GHS_POP_E2025_GLOBE_R2023A_54009_100/V1-0/tiles/"
        "GHS_POP_E2025_GLOBE_R2023A_54009_100_V1_0_R5_C29.zip"
    ),
    "GHS_POP_E2025_GLOBE_R2023A_54009_100_V1_0_R5_C30.tif": (
        "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/"
        "GHS_POP_E2025_GLOBE_R2023A_54009_100/V1-0/tiles/"
        "GHS_POP_E2025_GLOBE_R2023A_54009_100_V1_0_R5_C30.zip"
    ),
}

POPULATION_PORTAL_URL = "https://www.data.go.kr/data/15097972/fileData.do"
SCHOOLS_API_URL = "https://api.data.go.kr/openapi/tn_pubr_public_elesch_mskul_lc_api"


def build_plan(repo_root: Path) -> list[dict]:
    """다시 받아야 할 공개 원자료 목록을 순서대로 만든다 (순수 함수, 네트워크 없음)."""
    raw = Path(repo_root) / "data" / "raw"
    return [
        {
            "name": "osm_hwaseong_bike_20260914.json",
            "dest": raw / "osm_hwaseong_bike_20260914.json",
            "method": "overpass",
            "endpoint": OVERPASS_ENDPOINT,
            "query": OSM_BIKE_QUERY,
            "note": "OSM 자전거 시설 (ODbL) -- Claude_작업기록/scripts/osm_hwaseong_stats.py와 동일 쿼리",
        },
        {
            "name": "osm_roads/roads.json",
            "dest": raw / "osm_roads" / "roads.json",
            "method": "overpass",
            "endpoint": OVERPASS_ENDPOINT,
            "query": OSM_ROADS_QUERY,
            "note": "OSM 전체 도로망 (ODbL) -- 쿼리는 다운로드 기록으로부터 재구성됨(reconstructed)",
        },
        {
            "name": "osm_poi/stops.json",
            "dest": raw / "osm_poi" / "stops.json",
            "method": "overpass",
            "endpoint": OVERPASS_ENDPOINT,
            "query": OSM_STOPS_QUERY,
            "note": "OSM 버스정류장/역 POI (ODbL) -- 쿼리는 다운로드 기록으로부터 재구성됨(reconstructed)",
        },
        {
            "name": "admin/hwaseong_hjd_20260701.geojson",
            "dest": raw / "admin" / "hwaseong_hjd_20260701.geojson",
            "method": "http_get",
            "url": ADMDONGKOR_URL,
            "note": (
                "vuski/admdongkor 전국 파일 전체를 받는다. 저장소가 쓰는 파일은 "
                "sggnm에 '화성시'가 포함된 피처만 남긴 부분집합이므로, 받은 뒤 "
                "직접 필터링해야 한다(이 스크립트는 필터링하지 않음). "
                "라이선스가 저장소에 명시되어 있지 않다 -- 사람이 확인할 것"
            ),
        },
        *[
            {
                "name": f"ghsl/{fname}",
                "dest": raw / "ghsl" / fname,
                "method": "manual",
                "url": url,
                "note": (
                    "JRC FTP는 ZIP으로만 배포한다(CC BY 4.0). 자동 다운로드하지 않으니 "
                    "수동으로 받아 압축을 풀 것"
                ),
            }
            for fname, url in GHSL_TILE_URLS.items()
        ],
        {
            "name": "population/pop_raw.bin",
            "dest": raw / "population" / "pop_raw.bin",
            "method": "manual",
            "url": POPULATION_PORTAL_URL,
            "note": (
                "행정안전부 주민등록 인구(공공데이터포털 파일데이터). 세션/캡차가 있을 "
                "수 있어 자동화하지 않는다 -- 포털에서 수동으로 받을 것 "
                "(기준일 20260831 파일)"
            ),
        },
        {
            "name": "schools/page_*.json",
            "dest": raw / "schools",
            "method": "manual",
            "url": SCHOOLS_API_URL,
            "note": (
                "전국초중등학교위치표준데이터(api.data.go.kr). 서비스키가 필요해 "
                "자동화하지 않는다 -- 발급받은 키로 페이지네이션(10000행 단위)해 받을 것"
            ),
        },
    ]


def print_plan(plan: list[dict]) -> None:
    print(f"{len(plan)}개 항목 계획:")
    for item in plan:
        print(f"\n- {item['name']}")
        print(f"  method: {item['method']}")
        print(f"  dest:   {item['dest']}")
        if "endpoint" in item:
            print(f"  endpoint: {item['endpoint']}")
        if "query" in item:
            print(f"  query:  {item['query']}")
        if "url" in item:
            print(f"  url:    {item['url']}")
        print(f"  note:   {item['note']}")


def fetch_overpass(item: dict, force: bool) -> None:
    dest = item["dest"]
    if dest.exists() and not force:
        print(f"skip (exists): {dest}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = urllib.parse.urlencode({"data": item["query"]}).encode()
    print(f"fetching (overpass): {item['name']}\n  -> {dest}")
    with urllib.request.urlopen(
        urllib.request.Request(item["endpoint"], data=body), timeout=300
    ) as resp:
        data = resp.read()
    dest.write_bytes(data)


def fetch_http_get(item: dict, force: bool) -> None:
    dest = item["dest"]
    if dest.exists() and not force:
        print(f"skip (exists): {dest}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"fetching (GET): {item['name']}\n  -> {dest}")
    with urllib.request.urlopen(item["url"]) as resp:
        data = resp.read()
    dest.write_bytes(data)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", type=Path, default=config.ROOT)
    ap.add_argument("--dry-run", action="store_true", help="계획만 출력하고 아무 것도 받지 않는다")
    ap.add_argument("--force", action="store_true", help="기존 파일도 다시 받는다")
    args = ap.parse_args()

    plan = build_plan(args.repo_root)
    print_plan(plan)

    if args.dry_run:
        return

    for item in plan:
        if item["method"] == "overpass":
            fetch_overpass(item, force=args.force)
        elif item["method"] == "http_get":
            fetch_http_get(item, force=args.force)
        else:
            print(f"\n수동 처리 필요, 건너뜀: {item['name']} ({item['note']})")


if __name__ == "__main__":
    main()
