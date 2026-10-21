"""생활권 2곳 선정 — docs/preregistration/09_region_selection.md 규칙을 그대로 적용한다.

1. A구 = 구 전체 세그먼트 연장 대비 공식단일 비중 최대, B구 = OSM단일 비중 최대(같으면 2위)
2. 각 구의 읍면동마다 생활권 = 동 경계 + 1.5km 버퍼(화성시 경계로 자름)
   자격: 생활권 안 학교 ≥1, 버스정류장 ≥1, 세 층(합의·공식단일·OSM단일) 세그먼트 각 ≥10
   (판독 전이라 프레임 개수로 잠정 적용)
3. 순위: union 망, 학교, 2,500m에서 못 닿는 인구(pop − pop_reaching) 최대, 동률은 인구 큰 쪽. 2순위 병기.

--eligibility-only 는 1·2단계만 계산한다(접근성 결과 outputs/access_by_dong.csv 없이).
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from pyproj import Transformer
from shapely import wkt
from shapely.geometry import shape
from shapely.ops import transform, unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis import config  # noqa: E402
from analysis.numbers import record  # noqa: E402

# admdongkor sggnm은 "화성시효행구"처럼 공백 없이 온다 — 공백 제거 후 비교
DISTRICTS = {"화성시동탄구": "동탄", "화성시병점구": "병점", "화성시효행구": "효행", "화성시만세구": "만세"}
STRATA = ["합의", "공식단일", "OSM단일"]
SCHOOL_KINDS = {"초등학교", "중학교", "고등학교"}
BUFFER_M = 1500.0
MIN_SEGMENTS = 10
NOTE = "잠정(판독 전, 프레임 개수 기준) — docs/preregistration/09_region_selection.md"


def load_dongs(path):
    tf = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True).transform
    fc = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = []
    for f in fc["features"]:
        p = f["properties"]
        rows.append({
            "dong": p["adm_nm"].split()[-1],
            "district": DISTRICTS["".join(p["sggnm"].split())],
            "geom": transform(tf, shape(f["geometry"])),
        })
    return pd.DataFrame(rows)


# M7: 사전등록 문서(docs/preregistration/09_region_selection.md §1)는 구별
# 층 비중을 "numbers.csv로 계산"이라고 적었지만, 실제로는 이 함수가
# agreement_segments_b10.csv에서 직접 재계산한다. 값은 동등하다(같은
# 세그먼트 합계를 다른 소스에서 다시 구하는 것뿐) -- 문서 표현이 부정확할
# 뿐 계산 결과는 다르지 않다.
def district_shares(seg):
    seg = seg[seg["district"].isin(DISTRICTS.values())]
    km = seg.groupby(["district", "stratum"])["length_m"].sum().unstack(fill_value=0.0)
    return km.div(km.sum(axis=1), axis=0)


def pick_districts(shares):
    a = shares["공식단일"].idxmax()
    b_order = shares["OSM단일"].sort_values(ascending=False).index.tolist()
    b = b_order[0] if b_order[0] != a else b_order[1]
    return a, b


def eligibility(dongs, seg, dest, districts):
    city = unary_union(dongs["geom"].tolist())
    mids = shapely.line_interpolate_point(
        shapely.from_wkt(seg["geometry"].to_numpy()), 0.5, normalized=True)
    mx, my = shapely.get_x(mids), shapely.get_y(mids)
    school = dest[dest["kind"].isin(SCHOOL_KINDS)]
    bus = dest[dest["kind"] == "bus_stop"]
    out = []
    for _, d in dongs[dongs["district"].isin(districts)].iterrows():
        region = d["geom"].buffer(BUFFER_M).intersection(city)
        shapely.prepare(region)
        row = {"dong": d["dong"], "district": d["district"],
               "n_school": int(shapely.contains_xy(region, school["x"], school["y"]).sum()),
               "n_bus_stop": int(shapely.contains_xy(region, bus["x"], bus["y"]).sum())}
        inside = shapely.contains_xy(region, mx, my)
        for s in STRATA:
            row[f"n_seg_{s}"] = int((inside & (seg["stratum"].to_numpy() == s)).sum())
        row["eligible"] = (row["n_school"] >= 1 and row["n_bus_stop"] >= 1
                           and all(row[f"n_seg_{s}"] >= MIN_SEGMENTS for s in STRATA))
        out.append(row)
    return pd.DataFrame(out)


def rank(elig, access):
    # final-review I3: unreached를 detached(망 자체와 150m 넘게 떨어짐)와
    # attached_unreached(스냅은 되지만 cutoff를 넘음)로 분해해 함께 싣는다.
    # 순위 규칙 자체(사전등록된 total unreached 기준)는 바꾸지 않는다 --
    # access_by_dong.csv에 이미 그 두 컬럼이 있으면(Task 13b final-review
    # 이후 재실행분) 그대로 merge하고, 없으면(옛 access_by_dong.csv) 0으로
    # 채워 하위호환한다.
    a = access[(access["version"] == "union") & (access["kind"] == "school") & (access["cutoff"] == 2500)]
    has_breakdown = "pop_detached" in a.columns and "pop_attached_unreached" in a.columns
    cols = ["dong", "pop", "pop_reaching"]
    if has_breakdown:
        cols += ["pop_detached", "pop_attached_unreached"]
    a = a[cols].copy()
    a["unreached"] = a["pop"] - a["pop_reaching"]
    if has_breakdown:
        a = a.rename(columns={"pop_detached": "detached", "pop_attached_unreached": "attached_unreached"})
    else:
        a["detached"] = 0.0
        a["attached_unreached"] = a["unreached"]
    a = a[["dong", "pop", "unreached", "detached", "attached_unreached"]]

    m = elig.merge(a, on="dong", how="left", validate="one_to_one")
    missing = m.loc[m["unreached"].isna(), "dong"].tolist()
    if missing:
        raise ValueError(f"access_by_dong.csv에 없는 동: {missing}")
    # 사전등록 규칙(§3) 그대로: total unreached 내림차순, 동률은 인구 큰 쪽.
    # detached/attached_unreached는 정렬에 관여하지 않는 정보성 컬럼이다.
    m = m.sort_values(["district", "eligible", "unreached", "pop"], ascending=[True, False, False, False])
    m["rank_in_district"] = m.groupby("district").cumcount() + 1
    m.loc[~m["eligible"], "rank_in_district"] = np.nan
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=str(config.ROOT))
    ap.add_argument("--eligibility-only", action="store_true")
    args = ap.parse_args()
    root = Path(args.repo_root)

    seg = pd.read_csv(root / "outputs/private/agreement_segments_b10.csv")
    dest = pd.read_csv(root / "outputs/destinations_5186.csv")
    dongs = load_dongs(root / "data/raw/admin/hwaseong_hjd_20260701.geojson")

    shares = district_shares(seg)
    a, b = pick_districts(shares)
    print("구별 층 비중(연장 기준):")
    print(shares.round(3).to_string())
    print(f"A구(공식단일 비중 최대)={a}, B구(OSM단일 비중 최대)={b}")

    elig = eligibility(dongs, seg, dest, [a, b])
    if args.eligibility_only:
        print(elig.to_string(index=False))
        return

    access_path = root / "outputs/access_by_dong.csv"
    if not access_path.exists():
        raise SystemExit(f"{access_path} 없음 — Task 12c 실행 후 다시 돌리거나 --eligibility-only 사용")
    ranked = rank(elig, pd.read_csv(access_path))
    ranked.to_csv(root / "outputs/region_selection.csv", index=False, encoding="utf-8-sig")
    print(ranked.to_string(index=False))

    numbers = root / "outputs/numbers.csv"
    for label, dist in (("A", a), ("B", b)):
        top = ranked[(ranked["district"] == dist) & (ranked["rank_in_district"] <= 2)]
        if top.empty:
            raise SystemExit(f"{dist}에 자격을 갖춘 읍면동이 없음 — 사전등록 규칙상 2순위 구 검토 필요")
        for _, r in top.iterrows():
            k = int(r["rank_in_district"])
            # M7: int()는 절삭(truncate)이라 항상 실제보다 작게 기록된다 --
            # round()로 반올림한다.
            record(f"region_{label}{k}_unreached_school_c2500", round(float(r["unreached"])),
                   note=f"{dist} {r['dong']} (인구 {round(float(r['pop']))}); {NOTE}", path=numbers)
        record(f"region_{label}_share_{'official_only' if label == 'A' else 'osm_only'}",
               round(float(shares.loc[dist, '공식단일' if label == 'A' else 'OSM단일']), 4),
               note=f"{dist}; {NOTE}", path=numbers)


if __name__ == "__main__":
    main()
