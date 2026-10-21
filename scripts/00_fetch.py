"""화성시 WFS 원자료 다운로드.

사용법: python scripts/00_fetch.py [--force]

이미 존재하는 파일은 --force 없이는 건너뛴다. 네트워크 호출은 이 스크립트를
__main__으로 실행할 때만 일어난다 (테스트에서 import 시 호출되지 않음).
"""
import argparse
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import config

URLS = {
    config.OFFICIAL_LINES: (
        "https://air.hscity.go.kr/hsgisMap/gs1/geoserver/wfs?service=WFS&version=1.1.0"
        "&request=GetFeature&typeName=hsgisDB:RD.RDL_BYST_LS&maxFeatures=10000"
        "&outputFormat=application/json&srsName=EPSG:5186"
    ),
    config.OFFICIAL_POLYS: (
        "https://air.hscity.go.kr/hsgisMap/gs1/geoserver/wfs?service=WFS&version=1.1.0"
        "&request=GetFeature&typeName=hsgisDB:RD.RDL_BYCP_AS&maxFeatures=10000"
        "&outputFormat=application/json&srsName=EPSG:5186"
    ),
}


def fetch(dest: Path, url: str, force: bool = False) -> None:
    if dest.exists() and not force:
        print(f"skip (exists): {dest}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"fetching: {url}\n  -> {dest}")
    with urllib.request.urlopen(url) as resp:
        data = resp.read()
    dest.write_bytes(data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="기존 파일도 다시 받는다")
    args = parser.parse_args()

    for dest, url in URLS.items():
        fetch(dest, url, force=args.force)


if __name__ == "__main__":
    main()
