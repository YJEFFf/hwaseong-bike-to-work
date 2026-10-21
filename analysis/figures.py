"""공식 vs OSM 자전거망 출처 합의 그림.

헤드리스 파이프라인(테스트, 배치 스크립트)에서 호출되므로 pyplot import
전에 백엔드를 Agg로 고정한다 -- 인터랙티브 표시가 필요 없고 PNG만 저장한다.

주의: 이 함수가 그리는 지도 패널은 세그먼트 단위 원본 지오메트리를 실제
축척/등거리로 렌더링한다. 공식 GIS 재배포 허가가 확인되기 전까지
`outputs/figures/`는 커밋하지 않는다(.gitignore 참고) -- 그림 자체는
`scripts/02_agreement.py`로 언제든 재생성 가능하다.
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from shapely.geometry import box

# Okabe-Ito 색맹 안전 팔레트: 합의=진한 회색, 공식단일=파랑, OSM단일=주황.
STRATUM_COLORS = {
    "합의": "#4D4D4D",
    "공식단일": "#0072B2",
    "OSM단일": "#E69F00",
}
STRATUM_ORDER = ["합의", "공식단일", "OSM단일"]

ADMIN_BOUNDARY_COLOR = "#999999"

CAPTION = (
    "선: 화성시 생활공간 웹포털 자전거도로 GIS(2026-09-15 수집), "
    "© OpenStreetMap contributors (ODbL); "
    "행정경계: 통계청 기반(vuski/admdongkor)"
)


def _setup_korean_font():
    from matplotlib import font_manager
    installed = {font.name for font in font_manager.fontManager.ttflist}
    candidates = ["Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR"]
    available = next((name for name in candidates if name in installed), None)
    if available:
        plt.rcParams["font.family"] = available
    plt.rcParams["axes.unicode_minus"] = False


def _stratum_line_collections(df, bbox=None, linewidth=1.3):
    """stratum별 LineCollection 목록을 만든다. `bbox`(shapely geometry)가 주어지면
    그 bbox와 교차하는 세그먼트만 추려 그려 넣는다(인셋처럼 좁은 영역만 그릴 때
    불필요한 전체 지오메트리 순회/렌더링을 줄이기 위함)."""
    collections = []
    for stratum in STRATUM_ORDER:
        sub = df[df.stratum == stratum]
        if sub.empty:
            continue
        geoms = sub.geometry
        if bbox is not None:
            geoms = geoms[geoms.apply(lambda g: g.intersects(bbox))]
            if geoms.empty:
                continue
        segments = [list(geom.coords) for geom in geoms]
        lc = LineCollection(segments, colors=STRATUM_COLORS[stratum], linewidths=linewidth,
                             label=stratum, capstyle="round", zorder=2)
        collections.append(lc)
    return collections


def _draw_admin_boundaries(ax, admin_polys, linewidth=0.6):
    if not admin_polys:
        return
    for poly in admin_polys:
        geoms = poly.geoms if poly.geom_type == "MultiPolygon" else [poly]
        for g in geoms:
            xs, ys = g.exterior.xy
            ax.plot(xs, ys, color=ADMIN_BOUNDARY_COLOR, linewidth=linewidth, zorder=1)
            for interior in g.interiors:
                xs, ys = interior.xy
                ax.plot(xs, ys, color=ADMIN_BOUNDARY_COLOR, linewidth=linewidth, zorder=1)


def _draw_scale_bar(ax, length_m=5000.0, label="5 km"):
    xlim, ylim = ax.get_xlim(), ax.get_ylim()
    x0 = xlim[0] + 0.05 * (xlim[1] - xlim[0])
    y0 = ylim[0] + 0.05 * (ylim[1] - ylim[0])
    ax.plot([x0, x0 + length_m], [y0, y0], color="black", linewidth=2.5,
             solid_capstyle="butt", zorder=5)
    ax.text(x0 + length_m / 2, y0 + 0.015 * (ylim[1] - ylim[0]), label,
            ha="center", va="bottom", fontsize=8, zorder=5)


def _draw_map(ax, df, admin_polys=None):
    for lc in _stratum_line_collections(df):
        ax.add_collection(lc)

    _draw_admin_boundaries(ax, admin_polys)

    ax.autoscale_view()
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title("공식-OSM 자전거망 출처 합의 지도")

    _draw_scale_bar(ax)

    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(handles, labels, loc="upper left", fontsize=9, frameon=True)


def _draw_dongtan_inset(ax, df, admin_polys, admin_labels):
    """지도 패널 안에 동탄구 확대 인셋을 추가한다(밀집 구간 가독성 확보)."""
    if not admin_polys or not admin_labels or "동탄" not in admin_labels:
        return

    idx = admin_labels.index("동탄")
    dongtan_poly = admin_polys[idx]
    minx, miny, maxx, maxy = dongtan_poly.bounds
    bbox = box(minx, miny, maxx, maxy)

    axins = ax.inset_axes([0.56, 0.04, 0.42, 0.42])
    for lc in _stratum_line_collections(df, bbox=bbox, linewidth=1.0):
        axins.add_collection(lc)
    _draw_admin_boundaries(axins, admin_polys, linewidth=0.8)

    axins.set_xlim(minx, maxx)
    axins.set_ylim(miny, maxy)
    axins.set_aspect("equal")
    axins.set_xticks([])
    axins.set_yticks([])
    axins.set_title("동탄구 확대", fontsize=8)
    for spine in axins.spines.values():
        spine.set_edgecolor("#333333")
        spine.set_linewidth(1.0)

    ax.indicate_inset_zoom(axins, edgecolor="#333333")


def _draw_bars(ax, df, district_order):
    km = (
        df.assign(km=df.length_m / 1000.0)
        .groupby(["district", "stratum"])["km"]
        .sum()
        .unstack("stratum")
        .reindex(index=list(district_order), columns=STRATUM_ORDER, fill_value=0.0)
        .fillna(0.0)
    )

    y = np.arange(len(km.index))
    left = np.zeros(len(km.index))
    for stratum in STRATUM_ORDER:
        vals = km[stratum].to_numpy()
        ax.barh(y, vals, left=left, color=STRATUM_COLORS[stratum], label=stratum)
        left = left + vals

    ax.set_yticks(y)
    ax.set_yticklabels(km.index)
    ax.invert_yaxis()
    ax.set_xlabel("연장 (km)")
    ax.set_title("구별 출처 층(stratum)별 연장")
    ax.legend(loc="lower right", fontsize=9)


def fig1_disagreement(df, out_path, district_order=("동탄", "병점", "효행", "만세"),
                       admin_polys=None, admin_labels=None):
    """공식/OSM 출처 합의 그림1을 그려 `out_path`에 PNG(200dpi)로 저장한다.

    왼쪽: 세그먼트를 stratum별 색으로 칠한 지도(등거리축, 배경지도 없음).
    `admin_polys`/`admin_labels`(구별로 dissolve된 폴리곤과 이름)가 주어지면
    얇은 회색 구 경계선, 5km 축척바, 동탄구 확대 인셋을 함께 그린다.
    오른쪽: 구(district)별 stratum 누적 가로 막대(km).
    """
    _setup_korean_font()
    out_path = Path(out_path)

    fig, (ax_map, ax_bar) = plt.subplots(1, 2, figsize=(14, 7))

    _draw_map(ax_map, df, admin_polys=admin_polys)
    _draw_dongtan_inset(ax_map, df, admin_polys, admin_labels)
    _draw_bars(ax_bar, df, district_order)

    fig.suptitle("그림 1. 화성시 자전거망 출처 불일치(공식 GIS vs OSM)", fontsize=14)
    fig.text(0.5, 0.01, CAPTION, ha="center", va="bottom", fontsize=8, color="#555555")
    fig.tight_layout(rect=(0.0, 0.04, 1.0, 0.95))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=200)
    plt.close(fig)
    return out_path


ACCESS_VERSIONS = ["osm", "official", "union", "lowstress"]
ACCESS_VERSION_LABELS = {
    "osm": "OSM만",
    "official": "공식 GIS만",
    "union": "두 지도 합집합",
    "lowstress": "합집합+저위계 도로",
}
# Okabe-Ito 색맹 안전 팔레트에서 4개 선택 (fig1의 STRATUM_COLORS와는 별개 배색).
ACCESS_VERSION_COLORS = {
    "osm": "#E69F00",
    "official": "#0072B2",
    "union": "#009E73",
    "lowstress": "#D55E00",
}

ACCESS_DISTRICTS = ["dongtan", "byeongjeom", "hyohaeng", "manse"]
ACCESS_GROUP_ORDER = ["total", *ACCESS_DISTRICTS]
ACCESS_GROUP_LABELS = {
    "total": "전체",
    "dongtan": "동탄구",
    "byeongjeom": "병점구",
    "hyohaeng": "효행구",
    "manse": "만세구",
}

ACCESS_DETACHED_KEY = "access_union_school_c2500_pop_detached"
ACCESS_POP_TOTAL_KEY = "pop_total_hwaseong"

ACCESS_CAPTION_TEMPLATE = (
    "2.5km 거리 기준(실측 10분 아님, 접근·이탈 거리 포함). 인구: 주민등록인구 + GHSL 격자. "
    "자전거망: 화성시 생활공간 웹포털, © OpenStreetMap contributors(ODbL). "
    "합집합 기준 150m 내 허용 선 접속이 없는 모형 인구 {detached_share:.1f}%."
)


def _access_popshare_key(version, district=None):
    base = f"access_{version}_school_c2500_popshare"
    return base if district is None else f"{base}_{district}"


def _require_key(numbers, key):
    if key not in numbers:
        raise KeyError(f"numbers.csv에 키가 없음: {key}")
    return numbers[key]


def load_access_by_network(numbers: dict) -> dict:
    """numbers.csv를 읽어 만든 {key: value(str)} 딕셔너리에서 그림2용
    버전x구 학교(2.5km) 접근 인구 비율(%)을 뽑는다.

    반환값: {version: {"total": pct, "dongtan": pct, ...}} (버전은
    ACCESS_VERSIONS, 그룹은 ACCESS_GROUP_ORDER 순). 필요한 키가 numbers에
    없으면 어떤 키가 없는지 명시한 KeyError를 낸다.
    """
    values = {}
    for version in ACCESS_VERSIONS:
        per_version = {}
        for group in ACCESS_GROUP_ORDER:
            district = None if group == "total" else group
            key = _access_popshare_key(version, district)
            raw = _require_key(numbers, key)
            per_version[group] = float(raw) * 100.0
        values[version] = per_version
    return values


def load_detached_share(numbers: dict) -> float:
    """합집합(union) 망 기준 150m 밖(도달 불가) 인구 비율(%) =
    access_union_school_c2500_pop_detached / pop_total_hwaseong * 100."""
    detached = float(_require_key(numbers, ACCESS_DETACHED_KEY))
    pop_total = float(_require_key(numbers, ACCESS_POP_TOTAL_KEY))
    return detached / pop_total * 100.0


def plot_access_by_network(values: dict, detached_share: float, out_path) -> Path:
    """그림2: 자전거망 버전 x 구 별 학교(2.5km) 접근 인구 비율(%) 그룹 막대.

    values: {version: {"total": pct, "dongtan": pct, ...}} (load_access_by_network
    반환 형식). detached_share: 캡션에 쓸 합집합망 150m 밖 인구 비율(%).
    """
    _setup_korean_font()
    out_path = Path(out_path)

    groups = ACCESS_GROUP_ORDER
    x = np.arange(len(groups))
    n_versions = len(ACCESS_VERSIONS)
    width = 0.8 / n_versions

    fig, ax = plt.subplots(figsize=(11, 6.5))

    max_val = 0.0
    for i, version in enumerate(ACCESS_VERSIONS):
        vals = [values[version][g] for g in groups]
        max_val = max(max_val, max(vals))
        offset = (i - (n_versions - 1) / 2) * width
        bars = ax.bar(
            x + offset, vals, width,
            color=ACCESS_VERSION_COLORS[version],
            label=ACCESS_VERSION_LABELS[version],
        )
        for rect, v in zip(bars, vals):
            ax.text(
                rect.get_x() + rect.get_width() / 2, rect.get_height() + max(0.5, max_val * 0.01),
                f"{v:.1f}", ha="center", va="bottom", fontsize=8,
            )

    ax.set_xticks(x)
    ax.set_xticklabels([ACCESS_GROUP_LABELS[g] for g in groups])
    ax.set_ylabel("학교 2.5km 도달 인구 비율(%)")
    ax.set_ylim(0, max_val * 1.15 + 5)
    ax.set_title("그림 2. 어떤 자전거 지도로 보느냐에 따라 달라지는 학교 접근성(판독 전 잠정)")
    ax.legend(loc="upper right", fontsize=9, frameon=True)

    caption = ACCESS_CAPTION_TEMPLATE.format(detached_share=detached_share)
    fig.text(0.5, 0.01, caption, ha="center", va="bottom", fontsize=8, color="#555555")
    fig.tight_layout(rect=(0.0, 0.07, 1.0, 1.0))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=200)
    plt.close(fig)
    return out_path


PRIORITY_CAPTION = (
    "선(회색): 화성시 생활공간 웹포털 자전거도로 GIS(2026-09-15 수집) + "
    "© OpenStreetMap contributors (ODbL) 합집합망; "
    "행정경계: 통계청 기반(vuski/admdongkor); "
    "색: rule=reach 설정 집합에서 선택 빈도(확률 아님). "
    "같은 총연장 상한의 휴리스틱; 현장·실행 가능성 미확정"
)


def fig3_priority_links(network_lines, region_polys, priority_df, out_path,
                         inclusion_col="inclusion_reach"):
    """생활권 우선 연결 구간(priority links) 그림을 그려 out_path에 PNG(200dpi)로
    저장한다.

    network_lines: union 망 LineString 목록 (배경, 옅은 회색).
    region_polys: {region_label: shapely Polygon} -- 생활권 경계(얇은 선).
    priority_df: cand_id, region, x1, y1, x2, y2, inclusion_col 컬럼을 가진
        DataFrame (한 행 = 후보 링크 하나). 색은 inclusion_col 값(0~1)의
        연속 컬러맵.
    """
    _setup_korean_font()
    out_path = Path(out_path)

    fig, ax = plt.subplots(figsize=(9, 9))

    if network_lines:
        segments = [list(g.coords) for g in network_lines]
        ax.add_collection(LineCollection(segments, colors="#BBBBBB", linewidths=0.6, zorder=1))

    for poly in region_polys.values():
        geoms = poly.geoms if poly.geom_type == "MultiPolygon" else [poly]
        for g in geoms:
            if g.is_empty:
                continue
            xs, ys = g.exterior.xy
            ax.plot(xs, ys, color="#333333", linewidth=1.0, zorder=2)

    cmap = plt.get_cmap("plasma")
    if not priority_df.empty:
        link_segments = [
            [(r.x1, r.y1), (r.x2, r.y2)] for r in priority_df.itertuples()
        ]
        values = priority_df[inclusion_col].to_numpy(dtype=float)
        lc = LineCollection(link_segments, cmap=cmap, linewidths=3.0, zorder=3)
        lc.set_array(values)
        lc.set_clim(0.0, 1.0)
        ax.add_collection(lc)
        fig.colorbar(lc, ax=ax, label="selection frequency (rule=reach)", shrink=0.7)

    ax.autoscale_view()
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title("그림 3. 생활권 우선 연결 구간(priority links)")

    fig.text(0.5, 0.01, PRIORITY_CAPTION, ha="center", va="bottom", fontsize=7, color="#555555")
    fig.tight_layout(rect=(0.0, 0.05, 1.0, 1.0))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=200)
    plt.close(fig)
    return out_path
