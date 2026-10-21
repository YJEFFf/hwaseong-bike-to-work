"""판독 표본(Task 5b) 190지점 추출.

사용법:
    python scripts/03_sample.py [--repo-root PATH] [--dry-run]

입력(모두 --repo-root 기준, 기본값은 이 저장소 루트):
- outputs/private/agreement_segments_b10.csv  (Task 4 산출물; 컬럼: seg_id,
  origin, district, in_official_line, in_official_poly, in_osm, stratum,
  length_m, geometry(WKT, EPSG:5186))
- data/raw/yejun08_candidates.csv
- data/raw/화성_자전거도로면_5186.geojson
- data/raw/화성_자전거노선_5186.geojson
- data/raw/admin/hwaseong_hjd_20260701.geojson  (행정구역 경계, WGS84 -- Task 4
  analysis.agreement.district_for_point/DISTRICT_MAP가 쓰는 것과 동일한 소스)

출력:
- outputs/private/sample_points.csv  (git-ignored. 좌표 포함 -- 이용허락 확인
  전 비공개 유지)
- validation/sample_design.md  (공개 가능 요약: 층별 개수·seed·규칙만. 좌표 없음)

--dry-run 은 위 입력 파일 존재 여부만 점검하고 아무것도 쓰지 않는다(Task 4가
아직 병합되지 않은 브랜치에서도 안전하게 실행 가능).

표본 설계(브리프 원문, 그대로 구현):
1. 세그먼트: stratified_sample(frame, ["district","stratum"], n_per=10,
   seed=20260915), district in {동탄,병점,효행,만세} x stratum in
   {합의,공식단일,OSM단일} -> 최대 120. 점 = 세그먼트 중점. district는 Task 4가
   agreement_segments_b10.csv 생성 시 이미 각 세그먼트 자신의 중점으로 배정한
   값을 그대로 쓴다.
2. 후보지: yejun08_candidates.csv 커넥터 중점(5186), 30m 단일연결 클러스터링,
   클러스터당 대표 1개(distance_m 최소), district는 대표점 자신에
   analysis.agreement.district_for_point를 적용해 배정, district 층화표집
   n_per=10 -> 최대 40.
3. 섬 갭: dedupe한 공식 라인에 tol=10 성분 분석, 총 길이 1km 이상인 성분끼리의
   최근접 지점 간극 중 가장 작은 20개(부족하면 있는 만큼). district는 간극
   중점에 district_for_point를 적용.
4. 플레이스홀더: rut_nam에 "노선추가중" 포함 라인 5개(무작위, seed) +
   rmk_rmk에 "도시계획도" 포함 폴리곤 5개(무작위, seed) -> 최대 10. district는
   각 대표점에 district_for_point를 적용.

fix round 1 (task-5b-review.md I1): 4개 프레임 전부 district를 자신의 점으로
직접 배정한다(이전 버전은 island_gap/placeholder를 공란으로 남겼음). district
배정은 Task 4의 analysis.agreement.district_for_point(행정구역 경계 포함 여부
-> 200m 이내 최근접 -> "화성외") 하나로 통일한다 -- 자체 hjd_cde 접두어 판정을
따로 유지하지 않는다. "화성외"로 나오는 행은 후보지 프레임에서만 명시적으로
제외하고 개수를 보고한다(세그먼트는 이미 같은 기준으로 사전 필터됨; 섬 갭·
플레이스홀더는 공식 GIS 자체 기하에서 나온 점이라 화성외가 나올 가능성이
낮지만, 나오더라도 표본에서 빼지 않고 district="화성외"로 남겨 보고한다).
"""
import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import shapely.wkt
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import LineString, shape
from shapely.ops import transform as shp_transform, unary_union

from analysis import config
from analysis.agreement import DISTRICT_MAP, OUTSIDE_DISTRICT, district_for_point
from analysis.io_canon import load_fc
from analysis.numbers import record
from analysis.sample_frames import cluster_representatives, island_gaps, midpoint
from analysis.sampling import stratified_sample
from analysis.topo import dedupe

SEED = 20260915
DISTRICTS = ["동탄", "병점", "효행", "만세"]
STRATA = ["합의", "공식단일", "OSM단일"]
OUTSIDE_THRESHOLD_M = 200.0

TO_WGS84 = Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)
FROM_WGS84 = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True)

OUT_COLUMNS = ["pid", "sample_type", "district", "stratum", "x", "y", "lon", "lat",
               "source_ref", "vworld_url", "source_origin", "target_wkt"]


def input_paths(repo_root: Path) -> dict:
    return {
        "agreement": repo_root / "outputs" / "private" / "agreement_segments_b10.csv",
        "candidates": repo_root / "data" / "raw" / "yejun08_candidates.csv",
        "official_polys": repo_root / "data" / "raw" / "화성_자전거도로면_5186.geojson",
        "official_lines": repo_root / "data" / "raw" / "화성_자전거노선_5186.geojson",
        "admin": repo_root / "data" / "raw" / "admin" / "hwaseong_hjd_20260701.geojson",
    }


def check_inputs(repo_root: Path) -> dict:
    """각 입력 경로 존재 여부를 {이름: bool} 로 반환한다."""
    return {name: path.exists() for name, path in input_paths(repo_root).items()}


def to_wgs84(x: float, y: float):
    return TO_WGS84.transform(x, y)  # -> (lon, lat)


def vworld_url(lat: float, lon: float) -> str:
    return f"https://map.vworld.kr/map/maps.do?lat={lat:.6f}&lon={lon:.6f}&level=18"


def explode_lines(fc: dict) -> list:
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


def load_admin_districts(repo_root: Path):
    """행정구역 경계(WGS84)를 EPSG:5186으로 변환하고 sgg 코드 앞 5자리로 4개
    구(동탄/병점/효행/만세) 폴리곤을 dissolve해 반환한다. scripts/02_agreement.py
    의 load_admin_districts와 동일한 방식(같은 소스 파일, 같은 DISTRICT_MAP) --
    Task 4의 district 배정과 어긋나지 않도록 이 스크립트도 같은 방식으로
    admin_polys/admin_labels를 만든다."""
    fc = load_fc(input_paths(repo_root)["admin"])
    transformer = Transformer.from_crs(4326, 5186, always_xy=True)

    def to_5186(geom):
        return shp_transform(lambda x, y: transformer.transform(x, y), geom)

    groups: dict[str, list] = {}
    for feat in fc["features"]:
        code = str(feat["properties"].get("sgg") or "")[:5]
        district = DISTRICT_MAP.get(code)
        if district is None:
            continue
        groups.setdefault(district, []).append(to_5186(shape(feat["geometry"])))

    admin_polys, admin_labels = [], []
    for district in DISTRICTS:
        parts = groups.get(district)
        if not parts:
            continue
        admin_polys.append(unary_union(parts))
        admin_labels.append(district)
    return admin_polys, admin_labels


def build_segment_frame(repo_root: Path) -> list[dict]:
    path = input_paths(repo_root)["agreement"]
    df = pd.read_csv(path, encoding="utf-8-sig")
    df = df[df["district"].isin(DISTRICTS) & df["stratum"].isin(STRATA)].copy()

    sampled = stratified_sample(df, ["district", "stratum"], n_per=10, seed=SEED)

    rows = []
    for _, r in sampled.iterrows():
        geom = shapely.wkt.loads(r["geometry"])
        p = midpoint(geom)
        lon, lat = to_wgs84(p.x, p.y)
        rows.append({
            "sample_type": "segment", "district": r["district"], "stratum": r["stratum"],
            "x": p.x, "y": p.y, "lon": lon, "lat": lat, "source_ref": str(r["seg_id"]),
            "source_origin": r["origin"], "target_wkt": geom.wkt,
        })
    return rows


def build_candidate_frame(repo_root: Path, admin_tree, admin_polys, admin_labels):
    """반환: (rows, n_dropped_outside) -- n_dropped_outside는 "화성외"로 배정돼
    표본에서 제외된 대표 클러스터 수."""
    path = input_paths(repo_root)["candidates"]
    df = pd.read_csv(path, encoding="utf-8-sig")

    mid_x, mid_y = [], []
    for _, r in df.iterrows():
        fx, fy = FROM_WGS84.transform(float(r["from_lon"]), float(r["from_lat"]))
        tx, ty = FROM_WGS84.transform(float(r["to_lon"]), float(r["to_lat"]))
        p = midpoint(LineString([(fx, fy), (tx, ty)]))
        mid_x.append(p.x)
        mid_y.append(p.y)

    df = df.copy()
    df["_mid_x"] = mid_x
    df["_mid_y"] = mid_y
    df["distance_m"] = df["distance_m"].astype(float)

    reps_df = cluster_representatives(
        df, "_mid_x", "_mid_y", radius=30.0, rank_col="distance_m",
        admin_tree=admin_tree, admin_polys=admin_polys, admin_labels=admin_labels,
        outside_threshold=OUTSIDE_THRESHOLD_M,
    )

    n_outside = int((reps_df["district"] == OUTSIDE_DISTRICT).sum())
    reps_df = reps_df[reps_df["district"] != OUTSIDE_DISTRICT].copy()

    sampled = stratified_sample(reps_df, ["district"], n_per=10, seed=SEED)

    rows = []
    for _, r in sampled.iterrows():
        lon, lat = to_wgs84(r["_mid_x"], r["_mid_y"])
        rows.append({
            "sample_type": "candidate", "district": r["district"], "stratum": "",
            "x": r["_mid_x"], "y": r["_mid_y"], "lon": lon, "lat": lat,
            "source_ref": str(r["candidate_id"]), "source_origin": "",
            "target_wkt": LineString([
                FROM_WGS84.transform(float(r["from_lon"]), float(r["from_lat"])),
                FROM_WGS84.transform(float(r["to_lon"]), float(r["to_lat"]))]).wkt,
        })
    return rows, n_outside


def build_island_gap_frame(repo_root: Path, admin_tree, admin_polys, admin_labels) -> list[dict]:
    lines_fc = load_fc(input_paths(repo_root)["official_lines"])
    all_lines = explode_lines(lines_fc)
    dedup_lines = [all_lines[i] for i in dedupe(all_lines)]

    gaps = island_gaps(dedup_lines, tol=10.0, min_component_len=1000.0, k=20, include_geometry=True)

    rows = []
    for p, comp_a, comp_b, _gap_m, gap_line in gaps:
        lon, lat = to_wgs84(p.x, p.y)
        district = district_for_point(p, admin_tree, admin_polys, admin_labels,
                                       OUTSIDE_THRESHOLD_M)
        rows.append({
            "sample_type": "island_gap", "district": district, "stratum": "",
            "x": p.x, "y": p.y, "lon": lon, "lat": lat,
            "source_ref": f"comp{comp_a}_comp{comp_b}",
            "source_origin": "", "target_wkt": gap_line.wkt,
        })
    return rows


def build_placeholder_frame(repo_root: Path, lines_fc: dict, polys_fc: dict,
                             admin_tree, admin_polys, admin_labels,
                             seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)

    line_feats = [
        feat for feat in lines_fc["features"]
        if feat["properties"].get("rut_nam") and "노선추가중" in feat["properties"]["rut_nam"]
    ]
    poly_feats = [
        feat for feat in polys_fc["features"]
        if feat["properties"].get("rmk_rmk") and "도시계획도" in feat["properties"]["rmk_rmk"]
    ]

    picked_lines = rng.sample(line_feats, min(5, len(line_feats)))
    picked_polys = rng.sample(poly_feats, min(5, len(poly_feats)))

    rows = []
    for feat in picked_lines:
        geom = shape(feat["geometry"])
        if geom.geom_type == "MultiLineString":
            geom = max(geom.geoms, key=lambda g: g.length)  # 가장 긴 파트를 대표로
        p = midpoint(geom)
        lon, lat = to_wgs84(p.x, p.y)
        district = district_for_point(p, admin_tree, admin_polys, admin_labels,
                                       OUTSIDE_THRESHOLD_M)
        rows.append({
            "sample_type": "placeholder", "district": district, "stratum": "",
            "x": p.x, "y": p.y, "lon": lon, "lat": lat,
            "source_ref": f"line_{feat.get('id')}", "source_origin": "official",
            "target_wkt": geom.wkt,
        })
    for feat in picked_polys:
        p = midpoint(shape(feat["geometry"]))
        lon, lat = to_wgs84(p.x, p.y)
        district = district_for_point(p, admin_tree, admin_polys, admin_labels,
                                       OUTSIDE_THRESHOLD_M)
        rows.append({
            "sample_type": "placeholder", "district": district, "stratum": "",
            "x": p.x, "y": p.y, "lon": lon, "lat": lat,
            "source_ref": f"poly_{feat.get('id')}", "source_origin": "official",
            "target_wkt": shape(feat["geometry"]).wkt,
        })
    return rows


def assemble(rows: list[dict]) -> pd.DataFrame:
    out = []
    for i, r in enumerate(rows, start=1):
        row = dict(r)
        row["pid"] = f"S{i:03d}"
        row.setdefault("source_origin", "")
        row.setdefault("target_wkt", "")
        row["vworld_url"] = vworld_url(row["lat"], row["lon"])
        out.append(row)
    return pd.DataFrame(out, columns=OUT_COLUMNS)


def write_sample_design_md(path: Path, counts: dict, seg_strat_counts, cand_district_counts,
                            gap_district_counts, ph_district_counts,
                            n_cand_dropped_outside: int, seed: int) -> None:
    lines = [
        "# 판독 표본 설계 (Task 5b)",
        "",
        "좌표는 이 문서에 포함하지 않는다 (전체 좌표는 git-ignored "
        "`outputs/private/sample_points.csv` 에만 존재, 이용허락 확인 전 비공개).",
        "",
        f"- seed: {seed}",
        f"- 표본 총계: {counts['sample_n_total']}",
        f"  - segment: {counts['sample_n_segment']}",
        f"  - candidate: {counts['sample_n_candidate']}"
        f" ({n_cand_dropped_outside}개 클러스터가 \"화성외\"로 배정돼 표본에서 제외됨)",
        f"  - island_gap: {counts['sample_n_island_gap']}",
        f"  - placeholder: {counts['sample_n_placeholder']}",
        "",
        "## 1. 세그먼트 (district x stratum, n_per=10)",
        "",
        "| district | stratum | n |",
        "|---|---|---|",
    ]
    for (district, stratum), n in seg_strat_counts.items():
        lines.append(f"| {district} | {stratum} | {n} |")

    lines += [
        "",
        "## 2. 후보지 (district, n_per=10)",
        "",
        "| district | n |",
        "|---|---|",
    ]
    for district, n in cand_district_counts.items():
        lines.append(f"| {district} | {n} |")

    lines += [
        "",
        f"## 3. 섬 갭: {counts['sample_n_island_gap']}개 "
        "(tol=10m, 성분 총 길이>=1km, 최근접 간극 오름차순 상위 20, 부족 시 전량)",
        "",
        "| district | n |",
        "|---|---|",
    ]
    for district, n in gap_district_counts.items():
        lines.append(f"| {district} | {n} |")

    lines += [
        "",
        f"## 4. 플레이스홀더: {counts['sample_n_placeholder']}개 "
        "(rut_nam에 \"노선추가중\" 포함 라인 무작위 5 + rmk_rmk에 \"도시계획도\" 포함 폴리곤 무작위 5, seed 동일)",
        "",
        "| district | n |",
        "|---|---|",
    ]
    for district, n in ph_district_counts.items():
        lines.append(f"| {district} | {n} |")

    lines += [
        "",
        "## 규칙 요약",
        "",
        "1. 세그먼트: `stratified_sample(frame, [\"district\",\"stratum\"], n_per=10, "
        f"seed={seed})`, district in {{동탄,병점,효행,만세}} x stratum in "
        "{합의,공식단일,OSM단일}. 점 = 세그먼트 중점(정규화 0.5 지점). district는 "
        "agreement_segments_b10.csv 생성 시(Task 4) 이미 세그먼트 자신의 중점으로 "
        "배정된 값을 그대로 쓴다.",
        "2. 후보지: yejun08_candidates.csv 커넥터 중점(5186), 30m 단일연결(single-linkage) "
        "클러스터링, 클러스터당 대표 1개(distance_m 최소), 대표점 자신에 "
        "analysis.agreement.district_for_point(행정구역 경계 포함 -> 200m 이내 최근접 -> "
        f"\"화성외\")를 적용해 district 판정 후 district 층화표집(n_per=10, seed={seed}). "
        "\"화성외\"로 나온 대표는 표본에서 제외한다(개수는 위 표본 총계에 기록).",
        "3. 섬 갭: dedupe한 공식 라인에 tol=10m 성분 분석, 총 길이 1km 이상인 성분끼리의 "
        "최근접 지점 간극 중 가장 작은 20개(부족하면 있는 만큼). 점 = 최근접 지점 간극의 중점, "
        "district는 그 중점에 district_for_point를 적용(제외 없이 배정값 그대로 기록).",
        "4. 플레이스홀더: rut_nam에 \"노선추가중\" 포함 라인 5개(무작위, "
        f"seed={seed}) + rmk_rmk에 \"도시계획도\" 포함 폴리곤 5개(무작위, seed={seed}). "
        "라인 대표점은 line/Polygon 중점(MultiLineString 피처는 가장 긴 구성 파트의 중점을 "
        "사용 -- 공식 노선 281개 전부 MultiLineString이라 이 경로가 사실상 유일한 경로), "
        "폴리곤 대표점은 representative_point. district는 그 대표점에 district_for_point를 "
        "적용(제외 없이 배정값 그대로 기록).",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="판독 표본(Task 5b) 190지점 추출",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo-root", type=Path, default=config.ROOT,
                         help="입력/출력 기준 저장소 루트 (기본값: 이 저장소)")
    parser.add_argument("--dry-run", action="store_true",
                         help="입력 파일 존재 여부만 점검하고 아무것도 쓰지 않는다")
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    paths = input_paths(repo_root)
    status = check_inputs(repo_root)

    if args.dry_run:
        for name, ok in status.items():
            print(f"{'OK  ' if ok else 'MISS'} {name}: {paths[name]}")
        missing = [n for n, ok in status.items() if not ok]
        if missing:
            print(f"dry-run: {len(missing)} input(s) missing: {missing}")
        else:
            print("dry-run: all inputs present")
        return

    existing = repo_root / "outputs/private/sample_points.csv"
    if existing.exists():
        raise FileExistsError("Existing sample preserved. Reuse its frozen manifest or create a new version in a separate run.")
    if not status["agreement"]:
        raise FileNotFoundError(
            f"missing Task 4 output: {paths['agreement']} "
            "(run with --dry-run to check inputs without extracting)"
        )
    for name in ("candidates", "official_polys", "official_lines", "admin"):
        if not status[name]:
            raise FileNotFoundError(f"missing input: {paths[name]}")

    polys_fc = load_fc(paths["official_polys"])
    lines_fc = load_fc(paths["official_lines"])

    admin_polys, admin_labels = load_admin_districts(repo_root)
    admin_tree = STRtree(admin_polys) if admin_polys else None

    seg_rows = build_segment_frame(repo_root)
    cand_rows, n_cand_dropped_outside = build_candidate_frame(
        repo_root, admin_tree, admin_polys, admin_labels)
    gap_rows = build_island_gap_frame(repo_root, admin_tree, admin_polys, admin_labels)
    ph_rows = build_placeholder_frame(
        repo_root, lines_fc, polys_fc, admin_tree, admin_polys, admin_labels)

    df = assemble(seg_rows + cand_rows + gap_rows + ph_rows)

    out_path = repo_root / "outputs" / "private" / "sample_points.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")

    counts = {
        "sample_n_total": len(df),
        "sample_n_segment": len(seg_rows),
        "sample_n_candidate": len(cand_rows),
        "sample_n_island_gap": len(gap_rows),
        "sample_n_placeholder": len(ph_rows),
    }
    numbers_path = repo_root / "outputs" / "numbers.csv"
    for key, val in counts.items():
        record(key, val, "Task 5b 판독 표본 개수", path=numbers_path)
    record(
        "sample_candidate_dropped_outside_n", n_cand_dropped_outside,
        "Task 5b 후보지: district_for_point가 \"화성외\"로 배정해 표본에서 제외한 대표 클러스터 수",
        path=numbers_path,
    )

    seg_df = df[df["sample_type"] == "segment"]
    seg_strat_counts = seg_df.groupby(["district", "stratum"]).size()
    cand_df = df[df["sample_type"] == "candidate"]
    cand_district_counts = cand_df.groupby("district").size()
    gap_df = df[df["sample_type"] == "island_gap"]
    gap_district_counts = gap_df.groupby("district").size()
    ph_df = df[df["sample_type"] == "placeholder"]
    ph_district_counts = ph_df.groupby("district").size()

    write_sample_design_md(
        repo_root / "validation" / "sample_design.md", counts,
        seg_strat_counts, cand_district_counts, gap_district_counts, ph_district_counts,
        n_cand_dropped_outside, SEED,
    )

    print(counts)
    print(f"candidate clusters dropped as 화성외: {n_cand_dropped_outside}")
    print(f"wrote {out_path}")
    print(f"wrote {repo_root / 'validation' / 'sample_design.md'}")


if __name__ == "__main__":
    main()
