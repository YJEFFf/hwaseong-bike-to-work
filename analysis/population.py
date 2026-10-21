"""읍면동 인구 총량 로드 + GHSL 래스터 기반 dasymetric 출발지 점 생성.

- `load_dong_population`: 행정안전부 행정동별 주민등록인구 CSV(cp949)에서
  대상 시군구(sgg_prefix)의 읍면동 행만 남긴다 (읍면동명 빈 집계행 제외).
- `dasymetric_points`: GHSL GHS_POP 100m 래스터(ESRI:54009)의 셀 중심을
  EPSG:5186으로 변환해 읍면동 폴리곤에 배정하고, 읍면동별 합이 그 읍면동의
  주민등록 총량과 같아지도록 스케일한다. 래스터 합이 0인 읍면동은 총량을
  포함된 셀에 균등 배분한다.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio.windows import Window, from_bounds
from shapely.geometry import Point
from shapely.strtree import STRtree

_FROM_MOLLWEIDE = Transformer.from_crs("ESRI:54009", 5186, always_xy=True)
_TO_MOLLWEIDE = Transformer.from_crs(5186, "ESRI:54009", always_xy=True)


def load_dong_population(csv_path, sgg_prefix: str = "화성시") -> pd.DataFrame:
    df = pd.read_csv(csv_path, encoding="cp949", dtype=str)
    df = df[df["시군구명"].str.startswith(sgg_prefix, na=False)]
    df = df[df["읍면동명"].notna() & (df["읍면동명"].str.strip() != "")]

    out = pd.DataFrame({
        "code": df["행정기관코드"],
        "sgg": df["시군구명"],
        "dong": df["읍면동명"],
        "total": df["계"].astype(int),
    }).reset_index(drop=True)
    return out


def _cells_in_window(path, minx, miny, maxx, maxy):
    """단일 래스터에서 bbox(래스터 CRS 기준)와 겹치는 윈도우를 읽어
    (x_5186, y_5186, value) 배열을 반환한다. nodata 셀은 제외한다."""
    with rasterio.open(path) as ds:
        res_x, res_y = ds.res
        window = from_bounds(minx - res_x, miny - res_y, maxx + res_x, maxy + res_y, ds.transform)
        window = window.round_offsets().round_lengths()
        full = Window(0, 0, ds.width, ds.height)
        # bbox may not overlap this tile at all (e.g. dong polygons only span
        # one of several GHSL tiles) -- Window.intersection raises rather
        # than returning an empty window in that case.
        no_overlap = (
            window.col_off + window.width <= 0
            or window.row_off + window.height <= 0
            or window.col_off >= full.width
            or window.row_off >= full.height
        )
        if no_overlap:
            return np.empty(0), np.empty(0), np.empty(0)
        window = window.intersection(full)
        if window.width <= 0 or window.height <= 0:
            return np.empty(0), np.empty(0), np.empty(0)

        arr = ds.read(1, window=window).astype("float64")
        win_transform = ds.window_transform(window)
        nodata = ds.nodata

        rows, cols = np.indices(arr.shape)
        valid = np.ones(arr.shape, dtype=bool)
        if nodata is not None:
            valid &= arr != nodata
        valid &= ~np.isnan(arr)

        cols_v = cols[valid] + 0.5
        rows_v = rows[valid] + 0.5
        vals_v = arr[valid]

        a, b, c, d, e, f = (
            win_transform.a, win_transform.b, win_transform.c,
            win_transform.d, win_transform.e, win_transform.f,
        )
        xs_native = a * cols_v + b * rows_v + c
        ys_native = d * cols_v + e * rows_v + f

        xs_5186, ys_5186 = _FROM_MOLLWEIDE.transform(xs_native, ys_native)
        return np.asarray(xs_5186), np.asarray(ys_5186), vals_v


def dasymetric_points(raster_path, dong_polys: dict, dong_totals: dict) -> pd.DataFrame:
    paths = [raster_path] if isinstance(raster_path, (str, Path)) else list(raster_path)

    if any(not np.isfinite(v) or v < 0 for v in dong_totals.values()):
        raise ValueError("population totals must be finite and nonnegative")
    missing = [n for n, total in dong_totals.items() if total > 0 and n not in dong_polys]
    if missing:
        raise ValueError(f"positive population has no boundary: {missing}")
    if not dong_polys:
        return pd.DataFrame({"x": [], "y": [], "pop": [], "dong": []})

    names = list(dong_polys.keys())
    polys = [dong_polys[n] for n in names]
    minx, miny, maxx, maxy = polys[0].bounds
    for p in polys[1:]:
        b = p.bounds
        minx, miny = min(minx, b[0]), min(miny, b[1])
        maxx, maxy = max(maxx, b[2]), max(maxy, b[3])

    # Mollweide's axes are not aligned with EPSG:5186's away from Mollweide's
    # central meridian, so the reprojected bbox is sheared/rotated, not
    # axis-aligned. Transform all 4 corners (not just two opposite ones) and
    # pad generously so the native-CRS read window still fully covers the
    # dong polygons' extent.
    corners = [(minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy)]
    native_corners = [_TO_MOLLWEIDE.transform(x, y) for x, y in corners]
    native_minx = min(c[0] for c in native_corners) - 2000.0
    native_maxx = max(c[0] for c in native_corners) + 2000.0
    native_miny = min(c[1] for c in native_corners) - 2000.0
    native_maxy = max(c[1] for c in native_corners) + 2000.0

    xs_all, ys_all, vals_all = [], [], []
    for path in paths:
        xs, ys, vals = _cells_in_window(path, native_minx, native_miny, native_maxx, native_maxy)
        if len(xs):
            xs_all.append(xs)
            ys_all.append(ys)
            vals_all.append(vals)

    if not xs_all:
        if any(v > 0 for v in dong_totals.values()):
            raise ValueError("positive population has no raster coverage")
        return pd.DataFrame({"x": [], "y": [], "pop": [], "dong": []})

    xs_all = np.concatenate(xs_all)
    ys_all = np.concatenate(ys_all)
    vals_all = np.concatenate(vals_all)

    if not np.isfinite(vals_all).all() or (vals_all < 0).any():
        raise ValueError("invalid raster population values")
    cells = pd.DataFrame({"x": xs_all, "y": ys_all, "value": vals_all})
    if cells.groupby(["x", "y"])["value"].nunique().gt(1).any():
        raise ValueError("overlapping raster cells disagree")
    cells = cells.drop_duplicates(["x", "y"])
    xs_all, ys_all, vals_all = (cells[c].to_numpy() for c in ("x", "y", "value"))
    points = [Point(x, y) for x, y in zip(xs_all, ys_all)]
    tree = STRtree(polys)

    assigned_dong = np.full(len(points), -1, dtype=int)
    for i, pt in enumerate(points):
        for idx in tree.query(pt):
            if polys[idx].contains(pt):
                assigned_dong[i] = idx
                break

    out_x, out_y, out_pop, out_dong = [], [], [], []
    for idx, name in enumerate(names):
        mask = assigned_dong == idx
        if not mask.any():
            if dong_totals.get(name, 0) > 0:
                raise ValueError(f"positive population has no assigned raster cells: {name}")
            continue
        total = dong_totals.get(name, 0)
        if total <= 0:
            continue

        cell_vals = vals_all[mask]
        cell_x = xs_all[mask]
        cell_y = ys_all[mask]
        raster_sum = cell_vals.sum()

        if raster_sum > 0:
            pops = cell_vals / raster_sum * total
        else:
            pops = np.full(mask.sum(), total / mask.sum())

        keep = pops > 0
        out_x.extend(cell_x[keep])
        out_y.extend(cell_y[keep])
        out_pop.extend(pops[keep])
        out_dong.extend([name] * keep.sum())

    return pd.DataFrame({"x": out_x, "y": out_y, "pop": out_pop, "dong": out_dong})
