"""Build one overview map from the exact geometries in a distributed judging packet.

The packet intentionally omits the private source manifest. This script therefore
reads the target geometry already embedded in each distributed HTML card, joins it
to judging_sheet_B.csv by pid, and writes a local interactive map, GeoJSON, and PNG.
"""
import argparse
from collections import Counter
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import pandas as pd
from pyproj import Transformer
from shapely.geometry import LineString, Polygon, mapping, shape
from shapely.ops import transform, unary_union


TARGET_COLORS = {"#b23a48", "#e11d48", "#f43f5e"}
TYPE_COLORS = {
    "segment": "#2563eb",
    "candidate": "#dc2626",
    "island_gap": "#f59e0b",
    "placeholder": "#7c3aed",
}
TYPE_LABELS = {
    "segment": "구간 표본",
    "candidate": "연결 후보",
    "island_gap": "분리 성분 간극",
    "placeholder": "실재 여부 표본",
}
NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
TO_WGS84 = Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)


class TargetElementParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "polyline" and attrs.get("stroke", "").lower() in TARGET_COLORS:
            self.elements.append((tag, attrs))
        if tag == "path" and attrs.get("fill", "").lower() in TARGET_COLORS:
            self.elements.append((tag, attrs))


def _polyline(points):
    coords = []
    for token in points.split():
        x, y = token.split(",")
        coords.append((float(x), float(y)))
    return LineString(coords)


def _path_polygons(d):
    rings = []
    for part in re.split(r"(?=[Mm])", d):
        nums = [float(value) for value in re.findall(NUMBER, part)]
        if len(nums) < 6 or len(nums) % 2:
            continue
        coords = list(zip(nums[::2], nums[1::2]))
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        rings.append(Polygon(coords))
    if not rings:
        raise ValueError("target polygon path has no valid ring")
    geom = rings[0]
    for ring in rings[1:]:
        geom = geom.symmetric_difference(ring)
    return geom


def geometry_from_card(path):
    parser = TargetElementParser()
    parser.feed(Path(path).read_text(encoding="utf-8"))
    geoms = []
    for tag, attrs in parser.elements:
        geoms.append(_polyline(attrs["points"]) if tag == "polyline" else _path_polygons(attrs["d"]))
    if not geoms:
        raise ValueError(f"no target geometry in card: {path}")
    # A current card draws a line only once in the target colour. This also
    # tolerates disjoint polygon parts in older distributed cards.
    return geoms[0] if len(geoms) == 1 else unary_union(geoms)


def load_packet(sheet_path):
    sheet_path = Path(sheet_path).resolve()
    frame = pd.read_csv(sheet_path, encoding="utf-8-sig", keep_default_na=False)
    required = {"pid", "sample_type", "district", "target_packet", "judge_id"}
    if not required <= set(frame):
        raise ValueError(f"missing sheet columns: {sorted(required - set(frame))}")
    if len(frame) != frame.pid.nunique():
        raise ValueError("duplicate pid in judging sheet")
    records = []
    for row in frame.itertuples(index=False):
        card = sheet_path.parent / row.target_packet
        geom = geometry_from_card(card)
        records.append({
            "pid": row.pid,
            "sample_type": row.sample_type,
            "district": row.district,
            "judge_id": row.judge_id,
            "card": str(card),
            "geometry_5186": geom,
            "geometry": transform(TO_WGS84.transform, geom),
        })
    return records


def write_geojson(records, path):
    features = []
    for record in records:
        features.append({
            "type": "Feature",
            "properties": {key: record[key] for key in ("pid", "sample_type", "district")},
            "geometry": mapping(record["geometry"]),
        })
    collection = {"type": "FeatureCollection", "features": features}
    Path(path).write_text(json.dumps(collection, ensure_ascii=False), encoding="utf-8")
    return collection


def write_html(collection, path):
    counts = Counter(feature["properties"]["sample_type"] for feature in collection["features"])
    legend = "".join(
        f'<li><span style="background:{TYPE_COLORS[k]}"></span>{TYPE_LABELS[k]}: {counts[k]}개</li>'
        for k in TYPE_COLORS if counts[k]
    )
    data = json.dumps(collection, ensure_ascii=False).replace("</", "<\\/")
    colors = json.dumps(TYPE_COLORS, ensure_ascii=False)
    labels = json.dumps(TYPE_LABELS, ensure_ascii=False)
    content = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>전체 판독 대상 176곳</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>html,body,#map{{height:100%;margin:0}}.panel{{position:absolute;z-index:1000;top:14px;left:54px;
background:#fff;max-width:370px;padding:14px 16px;border-radius:10px;box-shadow:0 2px 14px #0004;
font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}h1{{font-size:18px;margin:0 0 7px}}
p{{margin:5px 0}}ul{{list-style:none;padding:0;margin:8px 0 0}}li span{{display:inline-block;width:22px;
height:5px;margin-right:8px;vertical-align:middle}}.note{{color:#4b5563;font-size:12px}}
@media(max-width:640px){{.panel{{right:10px;left:10px;max-width:none}}}}</style></head><body>
<div id="map"></div><section class="panel"><h1>전체 판독 대상 · 176곳</h1>
<p><strong>색 선·면과 검정 테두리</strong>가 실제 판독 대상입니다. 대상을 누르면 ID와 유형이 나옵니다.</p>
<p>A=준용, B=예준은 판독자 구분입니다. 두 사람 모두 같은 176곳을 판독합니다.</p>
<ul>{legend}</ul><p class="note">기존 배포 카드에 포함된 EPSG:5186 기하를 모아 표시했습니다.
바탕지도 © OpenStreetMap contributors.</p></section>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const data={data}; const colors={colors}; const labels={labels};
const map=L.map('map'); L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{
maxZoom:19,attribution:'&copy; OpenStreetMap contributors'}}).addTo(map);
const groups={{}};
for(const type of Object.keys(colors)){{
 const features={{type:'FeatureCollection',features:data.features.filter(f=>f.properties.sample_type===type)}};
 const group=L.layerGroup().addTo(map); groups[labels[type]]=group;
 L.geoJSON(features,{{style:()=>({{color:'#111827',weight:9,opacity:.88,fillOpacity:.12,fillColor:'#111827'}})}}).addTo(group);
 L.geoJSON(features,{{style:()=>({{color:colors[type],weight:5,opacity:1,fillOpacity:.42,fillColor:colors[type]}}),
 onEachFeature:(feature,layer)=>layer.bindPopup(`<strong>${{feature.properties.pid}}</strong><br>${{labels[type]}}<br>권역: ${{feature.properties.district}}`)}}).addTo(group);
}}
L.control.layers(null,groups,{{collapsed:false}}).addTo(map);
const bounds=L.geoJSON(data).getBounds(); map.fitBounds(bounds.pad(.08));
</script></body></html>'''
    Path(path).write_text(content, encoding="utf-8")


def _draw_geom(ax, geom, color, halo=False):
    width = 5.5 if halo else 2.2
    line_color = "#111827" if halo else color
    parts = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    for part in parts:
        if part.geom_type == "LineString":
            xs, ys = part.xy
            ax.plot(xs, ys, color=line_color, linewidth=width, alpha=.9, zorder=4 if halo else 5)
        elif part.geom_type == "Polygon":
            xs, ys = part.exterior.xy
            ax.plot(xs, ys, color=line_color, linewidth=width, alpha=.9, zorder=4 if halo else 5)
            if not halo:
                ax.fill(xs, ys, color=color, alpha=.22, zorder=3)


def write_png(records, path, context_path=None):
    matplotlib.rcParams["font.family"] = "AppleGothic"
    matplotlib.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(13, 11), dpi=180)
    if context_path and Path(context_path).exists():
        fc = json.loads(Path(context_path).read_text(encoding="utf-8"))
        for feature in fc.get("features", []):
            geom = shape(feature["geometry"])
            parts = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
            for part in parts:
                if part.geom_type == "LineString":
                    xs, ys = part.xy
                    ax.plot(xs, ys, color="#cbd5e1", linewidth=.35, alpha=.72, zorder=1)
    for record in records:
        _draw_geom(ax, record["geometry_5186"], TYPE_COLORS[record["sample_type"]], halo=True)
        _draw_geom(ax, record["geometry_5186"], TYPE_COLORS[record["sample_type"]])
    counts = Counter(record["sample_type"] for record in records)
    handles = [Line2D([0], [0], color=color, lw=4,
                      label=f"{TYPE_LABELS[kind]} {counts[kind]}개")
               for kind, color in TYPE_COLORS.items() if counts[kind]]
    ax.legend(handles=handles, loc="lower left", frameon=True, framealpha=.96)
    ax.set_title("화성시 전체 판독 대상 176곳", fontsize=20, weight="bold", pad=18)
    ax.text(.5, 1.005, "A=준용 · B=예준 | 두 판독자가 동일한 176곳을 독립 판독",
            transform=ax.transAxes, ha="center", va="bottom", fontsize=11, color="#374151")
    ax.set_aspect("equal")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--context-lines", type=Path)
    args = parser.parse_args()
    records = load_packet(args.sheet)
    if len(records) != 176:
        raise ValueError(f"expected frozen 176-target packet, got {len(records)}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    collection = write_geojson(records, args.output_dir / "전체_판독대상_176.geojson")
    write_html(collection, args.output_dir / "전체_판독대상_지도.html")
    write_png(records, args.output_dir / "전체_판독대상_지도.png", args.context_lines)
    print(f"wrote 176 targets to {args.output_dir}")


if __name__ == "__main__":
    main()
