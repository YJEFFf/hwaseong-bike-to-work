"""원자료(화성시 자전거 노선/도로면) 정합성 검증.

사용법: python scripts/01_verify.py

data/raw/의 두 GeoJSON을 읽어 canonical hash와 피처 수/총 연장을 계산하고,
기대값을 먼저 assert 한 뒤 그 결과를 repo root의 data_manifest.json에 병합해
기록한다(M6: 검증 실패 시 매니페스트를 건드리지 않기 위해 assert를 write보다
먼저 한다). 병합이므로 data_manifest.json에 이미 있는 다른 최상위 키(예: I2의
raw_inputs)는 지우지 않고 lines/polys/line_km/lines_hash/polys_hash/sources만
갱신한다. 동시에 outputs/numbers.csv에 핵심 수치를 기록한다.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import config
from analysis.io_canon import load_fc, verify_official
from analysis.numbers import record

MANIFEST_PATH = config.ROOT / "data_manifest.json"


def _load_existing_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {}
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def main() -> None:
    lines_fc = load_fc(config.OFFICIAL_LINES)
    polys_fc = load_fc(config.OFFICIAL_POLYS)

    r = verify_official(lines_fc, polys_fc)

    assert r["lines"] == 281 and r["polys"] == 5978 and abs(r["line_km"] - 318.982) < 0.01

    verified = {
        "lines": r["lines"],
        "polys": r["polys"],
        "line_km": r["line_km"],
        "lines_hash": r["lines_hash"],
        "polys_hash": r["polys_hash"],
        "sources": {
            "lines": config.OFFICIAL_LINES.name,
            "polys": config.OFFICIAL_POLYS.name,
        },
    }

    manifest = _load_existing_manifest()
    manifest.update(verified)
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    record("official_lines_n", r["lines"], "화성_자전거노선_5186.geojson feature 수")
    record("official_polys_n", r["polys"], "화성_자전거도로면_5186.geojson feature 수")
    record("official_line_km", r["line_km"], "공식 자전거 노선 총 연장(km)")

    # 콘솔 코드페이지가 cp949 등 UTF-8이 아닐 수 있으므로(Windows 기본값),
    # 여기서는 이 스크립트가 실제로 검증/갱신한 것만 ensure_ascii=True로 출력한다.
    # data_manifest.json 파일 자체는 위에서 이미 UTF-8로 정확히 썼다(I2의
    # raw_inputs 등 다른 최상위 키를 포함해서). 전체 매니페스트를 그대로
    # 콘솔에 다시 찍지 않는 이유는 그 안의 비-ASCII 문자(예: em dash)가
    # cp949 콘솔에서 print() 자체를 UnicodeEncodeError로 실패시키기 때문이다.
    print(json.dumps(verified, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
