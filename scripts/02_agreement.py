"""공식(화성시) 자전거 GIS와 OSM 자전거 시설의 출처 합의 분석.

사용법: python scripts/02_agreement.py

- 공식 자전거 노선(화성_자전거노선_5186.geojson)을 explode + dedupe(위상 정리,
  topo.dedupe, hausdorff<2m)한 라인 집합, 공식 자전거도로면(화성_자전거도로면_5186.geojson)
  폴리곤(버퍼 판정용), OSM 자전거 시설 way(is_bike_facility 필터,
  WGS84->EPSG:5186 변환, Transformer.from_crs(4326, 5186, always_xy=True))를 준비한다.
- 행정구역 경계(data/raw/admin/hwaseong_hjd_20260701.geojson, WGS84, sgg 코드로
  동/구 매핑)를 EPSG:5186으로 변환한 뒤 sgg 코드 앞 5자리로 4개 구(동탄/병점/
  효행/만세) 폴리곤을 dissolve한다. district 배정은 이제 화성시 자전거 GIS가
  아니라 이 행정구역 경계에 대한 포함 여부로 정한다(label_segments의
  admin_polys/admin_labels).
- buf_line in {5, 10, 20, 30}m(buf_poly=3m 고정) 각각에 대해 label_segments로
  세그먼트 프레임을 만든다:
  - {10, 20, 30}: 전국(화성시 전체) 합의 비중/OSM단일 연장 민감도
    (share_official_km_agree_bN, km_osm_only_bN)
  - {5, 10, 20}: 구별/층별 연장(km) 버퍼 민감도
    (agreement_km_{district}_{stratum}_bN)
  b=10 프레임은 추가로 예전 방식 키(km_{district}_{stratum}, 버퍼 접미사 없음)로도
  기록하고, outputs/private/agreement_segments_b10.csv(WKT 지오메트리)로 저장하고
  (공식 GIS 파생물 -- 재배포 허가 미확인, 커밋 금지, outputs/private/는 .gitignore),
  그림1(outputs/figures/fig1_map_disagreement.png, 역시 커밋 금지)을 생성한다.
- 화성시 4개 구 어디에도 속하지/가깝지 않은("화성외") 세그먼트 총 연장을
  agreement_segments_outside_km로 기록한다(b=10 기준).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyproj import Transformer
from shapely.geometry import LineString, shape
from shapely.ops import transform as shp_transform
from shapely.ops import unary_union

from analysis import config
from analysis.agreement import DISTRICT_MAP, OUTSIDE_DISTRICT, label_segments
from analysis.figures import fig1_disagreement
from analysis.io_canon import load_fc
from analysis.numbers import record
from analysis.osm_filter import is_bike_facility
from analysis.topo import dedupe

ADMIN_PATH = config.ROOT / "data" / "raw" / "admin" / "hwaseong_hjd_20260701.geojson"

BUF_POLY = 3.0
BUF_LINES_NATIONAL = [10.0, 20.0, 30.0]   # share_official_km_agree_bN / km_osm_only_bN
BUF_LINES_DISTRICT = [5.0, 10.0, 20.0]    # agreement_km_{district}_{stratum}_bN
ALL_BUFS = sorted(set(BUF_LINES_NATIONAL) | set(BUF_LINES_DISTRICT))
OUTSIDE_THRESHOLD_M = 200.0

DISTRICT_KEYS = {
    "동탄": "dongtan", "병점": "byeongjeom", "효행": "hyohaeng", "만세": "manse",
}
STRATUM_KEYS = {"합의": "agree", "공식단일": "official_only", "OSM단일": "osm_only"}


def explode_to_lines(fc: dict) -> list:
    """FeatureCollection의 (Multi)LineString 피처를 LineString 파트 리스트로 explode."""
    lines = []
    for feat in fc["features"]:
        geom = shape(feat["geometry"])
        if geom.geom_type == "MultiLineString":
            lines.extend(list(geom.geoms))
        elif geom.geom_type == "LineString":
            lines.append(geom)
        else:
            raise ValueError(f"unexpected geometry type: {geom.geom_type}")
    return lines


def load_official_lines() -> list:
    fc = load_fc(config.OFFICIAL_LINES)
    all_lines = explode_to_lines(fc)
    keep_idx = dedupe(all_lines)
    return [all_lines[i] for i in keep_idx]


def load_official_polys() -> list:
    fc = load_fc(config.OFFICIAL_POLYS)
    return [shape(feat["geometry"]) for feat in fc["features"]]


def load_osm_lines() -> list:
    with open(config.OSM_BIKE, encoding="utf-8") as f:
        data = json.load(f)

    transformer = Transformer.from_crs(4326, 5186, always_xy=True)
    lines = []
    for el in data["elements"]:
        if el.get("type") != "way" or not is_bike_facility(el.get("tags", {})):
            continue
        geometry = el.get("geometry") or []
        if len(geometry) < 2:
            continue
        coords = [transformer.transform(pt["lon"], pt["lat"]) for pt in geometry]
        lines.append(LineString(coords))
    return lines


def load_admin_districts():
    """행정구역 경계(WGS84)를 EPSG:5186으로 변환하고 sgg 코드 앞 5자리로
    4개 구(동탄/병점/효행/만세) 폴리곤을 dissolve해 반환한다."""
    fc = load_fc(ADMIN_PATH)
    transformer = Transformer.from_crs(4326, 5186, always_xy=True)

    def to_5186(geom):
        return shp_transform(lambda x, y: transformer.transform(x, y), geom)

    groups: dict[str, list] = {}
    for feat in fc["features"]:
        code = str(feat["properties"].get("sgg") or "")[:5]
        district = DISTRICT_MAP.get(code)
        if district is None:
            continue  # 알려진 4개 구 코드 외에는 없어야 정상(방어적 스킵)
        groups.setdefault(district, []).append(to_5186(shape(feat["geometry"])))

    admin_polys, admin_labels = [], []
    for district in ["동탄", "병점", "효행", "만세"]:
        parts = groups.get(district)
        if not parts:
            continue
        admin_polys.append(unary_union(parts))
        admin_labels.append(district)
    return admin_polys, admin_labels


def main() -> None:
    official_lines = load_official_lines()
    official_polys = load_official_polys()
    osm_lines = load_osm_lines()
    admin_polys, admin_labels = load_admin_districts()

    official_km_total = sum(g.length for g in official_lines) / 1000.0
    print(f"official lines: {len(official_lines)} ({official_km_total:.3f} km after dedupe)")
    print(f"official polys: {len(official_polys)}")
    print(f"osm bike facility ways: {len(osm_lines)}")
    print(f"admin districts: {admin_labels}")

    b10_df = None
    for b in ALL_BUFS:
        df = label_segments(
            official_lines, official_polys, osm_lines,
            buf_line=b, buf_poly=BUF_POLY,
            admin_polys=admin_polys, admin_labels=admin_labels,
            outside_threshold=OUTSIDE_THRESHOLD_M,
        )
        bi = int(b)

        if b in BUF_LINES_NATIONAL:
            agree_km = df.loc[df.stratum == "합의", "length_m"].sum() / 1000.0
            osm_only_km = df.loc[df.stratum == "OSM단일", "length_m"].sum() / 1000.0
            share = agree_km / official_km_total if official_km_total else float("nan")

            record(
                f"share_official_km_agree_b{bi}", round(share, 4),
                f"buf_line={bi}m, buf_poly={BUF_POLY}m: 공식 연장 중 OSM과 합의(dwithin)하는 비중",
            )
            record(
                f"km_osm_only_b{bi}", round(osm_only_km, 3),
                f"buf_line={bi}m, buf_poly={BUF_POLY}m: OSM단일 세그먼트 총 연장(km)",
            )
            print(
                f"b={bi}m: official_km={official_km_total:.3f} agree_km={agree_km:.3f} "
                f"osm_only_km={osm_only_km:.3f} share_agree={share:.4f}"
            )

        if b in BUF_LINES_DISTRICT:
            district_stratum_km = (
                df.assign(km=df.length_m / 1000.0)
                .groupby(["district", "stratum"])["km"].sum()
            )
            print(f"구별/층별 연장(km, buf_line={bi}m):")
            for district, dkey in DISTRICT_KEYS.items():
                for stratum, skey in STRATUM_KEYS.items():
                    km = float(district_stratum_km.get((district, stratum), 0.0))
                    record(
                        f"agreement_km_{dkey}_{skey}_b{bi}", round(km, 3),
                        f"buf_line={bi}m: {district}/{stratum} 세그먼트 총 연장(km)",
                    )
                    print(f"  {district}/{stratum} ({dkey}/{skey}): {km:.3f} km")

        if bi == 10:
            b10_df = df

    # 구별/층별 연장(km) -- buf_line=10m 기준, 기존(버퍼 접미사 없는) 키도 유지
    district_stratum_km_b10 = (
        b10_df.assign(km=b10_df.length_m / 1000.0)
        .groupby(["district", "stratum"])["km"].sum()
    )
    print("\n구별/층별 연장(km, buf_line=10m, 기존 키):")
    for district, dkey in DISTRICT_KEYS.items():
        for stratum, skey in STRATUM_KEYS.items():
            km = float(district_stratum_km_b10.get((district, stratum), 0.0))
            record(
                f"km_{dkey}_{skey}", round(km, 3),
                f"buf_line=10m: {district}/{stratum} 세그먼트 총 연장(km)",
            )
            print(f"  {district}/{stratum} ({dkey}/{skey}): {km:.3f} km")

    outside_km = b10_df.loc[b10_df.district == OUTSIDE_DISTRICT, "length_m"].sum() / 1000.0
    record(
        "agreement_segments_outside_km", round(outside_km, 3),
        f"buf_line=10m: 4개 구 어디에도 속하지/{OUTSIDE_THRESHOLD_M:.0f}m 이내로 가깝지 않은(\"{OUTSIDE_DISTRICT}\") 세그먼트 총 연장(km)",
    )
    print(f"\n{OUTSIDE_DISTRICT} (구역 밖) 연장: {outside_km:.3f} km")

    # b=10 프레임을 WKT로 저장 -- 공식 GIS 파생물, 재배포 허가 미확인이므로 커밋 금지
    private_dir = config.OUTPUTS / "private"
    private_dir.mkdir(parents=True, exist_ok=True)
    out_csv = private_dir / "agreement_segments_b10.csv"
    out_df = b10_df.copy()
    out_df["geometry"] = out_df["geometry"].apply(lambda g: g.wkt)
    out_df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\nwrote {out_csv} ({len(out_df)} rows, not committed -- see .gitignore)")

    # 그림1 -- outputs/figures/도 커밋 금지(.gitignore): 공식 GIS 원 지오메트리 노출 방지
    fig_path = config.FIGURES / "fig1_map_disagreement.png"
    fig1_disagreement(b10_df, fig_path, admin_polys=admin_polys, admin_labels=admin_labels)
    print(f"wrote {fig_path} (not committed -- see .gitignore)")


if __name__ == "__main__":
    main()
