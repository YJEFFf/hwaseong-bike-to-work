"""Private, frozen blind-ID manifests and neutral target geometry cards.

No aerial imagery is fetched or redistributed. The card identifies the full
target, not its source/stratum. Reviewers still need authorised external imagery.
"""
import hashlib
from html import escape
import json
from pathlib import Path
import secrets
import pandas as pd
import shapely
from analysis.judgments import validate_matching_ids


def read_manifest(private_dir):
    private_dir = Path(private_dir)
    path = private_dir / "judging_manifest.csv"
    meta = json.loads((private_dir / "judging_manifest.json").read_text())
    if hashlib.sha256(path.read_bytes()).hexdigest() != meta["manifest_sha256"]:
        raise ValueError("frozen manifest was modified")
    return pd.read_csv(path, keep_default_na=False)


def create_manifest(samples, private_dir):
    private_dir = Path(private_dir)
    required = {"pid", "sample_type", "district", "vworld_url", "target_wkt", "source_origin"}
    if not required <= set(samples):
        raise ValueError("target geometry missing: do not distribute midpoint-only legacy sheets")
    validate_matching_ids(samples, samples)
    # Freeze target order/metadata hash independently from random blind IDs.
    payload = samples.fillna("").sort_values("pid").to_csv(index=False).encode()
    digest = hashlib.sha256(payload).hexdigest()
    manifest_path = private_dir / "judging_manifest.csv"
    meta_path = private_dir / "judging_manifest.json"
    if manifest_path.exists():
        meta = json.loads(meta_path.read_text())
        if meta["sample_sha256"] != digest:
            raise ValueError("sample changed after freezing; use a separately approved new packet version")
        existing = pd.read_csv(manifest_path, keep_default_na=False)
        if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != meta["manifest_sha256"]:
            raise ValueError("frozen manifest was modified")
        return existing
    for text in samples.target_wkt:
        geom = shapely.from_wkt(text)
        if geom.is_empty or not geom.is_valid or geom.geom_type not in {"LineString", "Polygon", "MultiPolygon"}:
            raise ValueError("invalid target geometry")
    manifest = samples.rename(columns={"pid": "legacy_pid"}).copy().fillna("")
    manifest["pid"] = ["P-" + secrets.token_hex(8) for _ in range(len(manifest))]
    manifest["target_packet"] = "judging_targets/" + manifest.pid + ".html"
    private_dir.mkdir(parents=True, exist_ok=True)
    cards = private_dir / "judging_targets"
    cards.mkdir(exist_ok=True)
    for row in manifest.itertuples():
        geom = shapely.from_wkt(row.target_wkt)
        xmin, ymin, xmax, ymax = geom.bounds
        size = max(xmax-xmin, ymax-ymin, 1.)
        pad = size*.20
        # SVG y axis is reversed using a group transform; north remains up.
        # Draw the same geometry three times. The two wider strokes form a
        # screen-space border around the exact target without pretending that
        # the GIS centreline has a measured physical width.
        outline = geom if geom.geom_type == "LineString" else geom.boundary
        outer_svg = outline.svg(scale_factor=max(size/85., .18), stroke_color="#ffffff")
        border_svg = outline.svg(scale_factor=max(size/125., .14), stroke_color="#111827")
        target_svg = geom.svg(scale_factor=max(size/210., .10), **(
            {"stroke_color": "#e11d48"} if geom.geom_type == "LineString" else {"fill_color": "#f43f5e"}))
        marks = ""
        mark_labels = ""
        if geom.geom_type == "LineString":
            for name, (x, y, *_) in zip(("시점", "종점"), (geom.coords[0], geom.coords[-1])):
                marks += (f'<circle cx="{x}" cy="{y}" r="{size*.018}" fill="#facc15" '
                          f'stroke="#111827" stroke-width="{size*.006}"/>')
                mark_labels += (f'<text x="{x + size*.025}" y="{-y - size*.018}" '
                                f'font-size="{size*.045}" font-weight="700" fill="#111827" '
                                f'paint-order="stroke" stroke="white" stroke-width="{size*.008}">'
                                f'{name}</text>')
        frame_x = xmin - size*.075
        frame_y = -ymax - size*.075
        frame_w = xmax - xmin + size*.15
        frame_h = ymax - ymin + size*.15
        card = (f'<!doctype html><meta charset="utf-8"><title>{escape(row.pid)}</title>'
                '<style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;'
                'max-width:980px;margin:24px auto;color:#111827}svg{background:#f3f4f6;border:1px solid #9ca3af}'
                '.legend{display:flex;gap:20px;flex-wrap:wrap}.chip{display:inline-flex;align-items:center;gap:7px}'
                '.line{display:inline-block;width:34px;height:7px;background:#e11d48;border:2px solid #111827}'
                '.box{display:inline-block;width:30px;height:18px;border:3px dashed #f59e0b}</style>'
                f'<h1>{escape(row.pid)}</h1><p><strong>판독 대상:</strong> 분홍색 선·면 전체. '
                '검정 테두리는 대상을 알아보기 위한 강조선이며 실제 시설 폭이 아닙니다.</p>'
                '<p class="legend"><span class="chip"><span class="line"></span>판독 대상 기하</span>'
                '<span class="chip"><span class="box"></span>대상 전체를 감싼 판독 범위</span></p>'
                f'<p>북쪽 ↑ · 단위 m · 출처 비공개</p>'
                f'<svg width="900" height="600" viewBox="{xmin-pad} {-ymax-pad} {xmax-xmin+2*pad} {ymax-ymin+2*pad}">'
                f'<rect data-role="target-border" x="{frame_x}" y="{frame_y}" width="{frame_w}" '
                f'height="{frame_h}" fill="none" stroke="#f59e0b" stroke-width="{size*.012}" '
                f'stroke-dasharray="{size*.035} {size*.022}"/>'
                f'<g data-role="target-geometry" transform="scale(1,-1)">'
                f'{outer_svg}{border_svg}{target_svg}{marks}</g>{mark_labels}</svg>'
                f'<p>좌표계 EPSG:5186. 범위: {xmin:.2f}, {ymin:.2f} — {xmax:.2f}, {ymax:.2f}</p>'
                f'<p><a href="{escape(row.vworld_url, quote=True)}">외부 지도 중심점 열기</a>'
                ' — 중심점만 보지 말고 위 대상 전체와 양 끝점을 대조하세요.</p>'
                '<p><strong>A/B는 대상의 양끝이나 등급이 아니라 판독자 구분입니다.</strong> '
                '두 판독자는 이 카드와 같은 대상을 각각 독립 판독합니다.</p>'
                '<p>항공영상은 포함하지 않았습니다. 표식/폭/단절/통행 허용을 확인하지 못하면 판독불가.</p>')
        (private_dir / row.target_packet).write_text(card)
    manifest.to_csv(manifest_path, index=False, encoding="utf-8-sig")
    meta_path.write_text(json.dumps({"schema_version": 2, "sample_sha256": digest,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()}, indent=2)+"\n")
    return manifest


def validate_completed_sheet(frame, manifest, judge):
    validate_matching_ids(frame, manifest, name=f"judge {judge}")
    required = {"confidence", "imagery_date", "imagery_source", "passability", "judge_id"}
    if not required <= set(frame):
        raise ValueError("missing confidence/date/source/passability/judge metadata")
    joined = frame.merge(manifest, on="pid", validate="one_to_one", suffixes=("", "_frozen"))
    for col in ("sample_type", "district", "vworld_url", "target_packet"):
        if col not in joined or not joined[col].eq(joined[col+"_frozen"]).all():
            raise ValueError(f"frozen metadata mismatch: {col}")
    if not frame.judge_id.eq(judge).all():
        raise ValueError("judge_id mismatch")
    confidence = pd.to_numeric(frame.confidence, errors="coerce")
    if not confidence.isin([1, 2, 3]).all():
        raise ValueError("confidence must be 1, 2 or 3")
    from analysis.judgments import PASSABILITY
    if not frame.passability.isin(PASSABILITY).all():
        raise ValueError("passability missing or invalid")
    for col in ("imagery_date", "imagery_source"):
        if frame[col].isna().any() or frame[col].astype(str).str.strip().eq("").any():
            raise ValueError(f"{col} required (record 'unknown' explicitly if unavailable)")
