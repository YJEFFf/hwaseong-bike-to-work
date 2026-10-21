"""Comparable source-network scenarios with explicit line access and transition QA.

Primary table: unbridged networks under the same routing rules. The 10m bridge
scenario is written separately for *all* sources. Human corrections are explicit
inputs; partial sample coverage never certifies the whole city network.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
from analysis import config
from analysis.access import assign_districts, dong_district_map
from analysis.numbers import record
from analysis.routing import build_access_network, load_features, transition_summary

ORIGINS_CSV = "outputs/origins_5186.csv"
DESTS_CSV = "outputs/destinations_5186.csv"
POP_CSV_REL = "data/raw/population/pop_raw.bin"
ADMIN_REL = "data/raw/admin/hwaseong_hjd_20260701.geojson"
REQUIRED_INPUTS = [
    ORIGINS_CSV, DESTS_CSV, "data/raw/화성_자전거노선_5186.geojson",
    "data/raw/osm_hwaseong_bike_20260914.json", POP_CSV_REL,
    "data/raw/osm_roads/roads.json",
]
VERSIONS = ["osm", "official", "union", "lowstress"]
KINDS = ["school", "bus_stop", "station"]
SCHOOL_KINDS = {"초등학교", "중학교", "고등학교"}
CUTOFFS = [2000., 2500., 3000.]
DISTRICTS = ["dongtan", "byeongjeom", "hyohaeng", "manse"]
BRIDGES = [0., 10.]
DISTRICT_CUTOFF = 2500.
NOTE = "모형 잠정: 선 접속비용 포함, 동일층 교차·150m 직선 접근 가정; 실제 통학/이용/순탄소 아님"


def missing_inputs(repo_root, prepared_inputs=False):
    paths = list(REQUIRED_INPUTS)
    if prepared_inputs:
        paths.remove(POP_CSV_REL)
        paths.append(ADMIN_REL)
    return [p for p in paths if not (Path(repo_root) / p).is_file()]


def _origins(root, prepared_inputs):
    origins = pd.read_csv(root / ORIGINS_CSV)
    if origins[["x", "y", "pop"]].isna().any().any() or (origins["pop"] < 0).any():
        raise ValueError("invalid origin coordinates/population")
    if not np.isfinite(origins[["x", "y", "pop"]].to_numpy(float)).all():
        raise ValueError("nonfinite origin input")
    if origins[["x", "y"]].duplicated().any() or origins["pop"].sum() <= 0:
        raise ValueError("duplicate origin coordinates or zero population")
    if prepared_inputs:
        names = {"동탄구": "dongtan", "병점구": "byeongjeom", "효행구": "hyohaeng", "만세구": "manse"}
        fc = json.loads((root / ADMIN_REL).read_text())
        mapping = {}
        for feature in fc["features"]:
            props = feature["properties"]
            districts = [value for suffix, value in names.items() if suffix in props.get("sggnm", "")]
            if len(districts) == 1:
                mapping[props["adm_nm"].split()[-1]] = districts[0]
    else:
        mapping = dong_district_map(pd.read_csv(root / POP_CSV_REL, encoding="cp949"))
    origins["district"] = assign_districts(origins["dong"], mapping)
    return origins


def dest_xy_for_kind(dests, kind):
    mask = dests["kind"].isin(SCHOOL_KINDS) if kind == "school" else dests["kind"].eq(kind)
    return dests.loc[mask, ["x", "y"]]


def run_analysis(repo_root, corrections=None, prepared_inputs=False):
    root = Path(repo_root)
    numbers = root / "outputs/numbers.csv"
    origins = _origins(root, prepared_inputs)
    dests = pd.read_csv(root / DESTS_CSV)
    pop = origins["pop"].to_numpy(float)
    rows, all_rows, transitions, bridge_rows, masks = [], [], [], [], {}
    correction_note = "부분 표본 판독 반영" if corrections else "판독 미반영"
    for version in VERSIONS:
        features = load_features(root, version, corrections)
        net = build_access_network(features, origins[["x", "y"]], dests[["x", "y"]])
        for bridge_m in BRIDGES:
            links = net.bridge_links(bridge_m)
            graph = net.with_links(links) if links else net.graph
            bridge_rows.append({"version": version, "bridge_m": bridge_m, "n": len(links),
                                "length_m": sum(x[2] for x in links)})
            bridge_rows[-1]["scenario"] = "unverified_geometric_correction" if bridge_m else "no_bridges"
            for kind in KINDS:
                dmask = dests["kind"].isin(SCHOOL_KINDS) if kind == "school" else dests["kind"].eq(kind)
                distance = net.approach_distances(dmask, max(CUTOFFS), graph)
                for cutoff in CUTOFFS:
                    mask = distance <= cutoff
                    masks[(version, bridge_m, kind, cutoff)] = mask
                    detached = net.detached
                    grouped = origins.assign(
                        pop_reaching=pop * mask, pop_detached=pop * detached,
                        pop_attached_unreached=pop * ~mask * ~detached,
                    ).groupby(["dong", "district"], as_index=False).agg(
                        pop=("pop", "sum"), pop_reaching=("pop_reaching", "sum"),
                        pop_detached=("pop_detached", "sum"),
                        pop_attached_unreached=("pop_attached_unreached", "sum"),
                    )
                    grouped["version"], grouped["kind"], grouped["cutoff"] = version, kind, int(cutoff)
                    all_rows.append(grouped.assign(bridge_m=bridge_m, correction_state=correction_note))
                    if bridge_m != 0:
                        continue
                    rows.append(grouped)
                    note = f"{version}/{kind}, cutoff={cutoff:g}m, no gap bridges; {NOTE}; {correction_note}"
                    record(f"access_{version}_{kind}_c{int(cutoff)}_popshare",
                           float(pop[mask].sum() / pop.sum()), note, path=numbers)
                    if cutoff == DISTRICT_CUTOFF:
                        record(f"access_{version}_{kind}_c2500_pop_detached", float(pop[detached].sum()),
                               "현재 선 접속 가정에서 미접속; 확장/연결의 영구 불가능 인구 아님", path=numbers)
                        record(f"access_{version}_{kind}_c2500_pop_attached_unreached",
                               float(pop[~detached & ~mask].sum()), note, path=numbers)
                        for district in DISTRICTS:
                            inside = origins["district"].eq(district).to_numpy()
                            denominator = pop[inside].sum()
                            if denominator <= 0:
                                raise ValueError(f"district {district} has zero population")
                            record(f"access_{version}_{kind}_c2500_popshare_{district}",
                                   float(pop[inside & mask].sum() / denominator), note, path=numbers)
            if bridge_m == 0:
                record(f"access_{version}_bridges_n", 0, "primary scenario uses no gap bridges", path=numbers)
                record(f"access_{version}_bridges_km", 0, "primary scenario uses no gap bridges", path=numbers)
        # Individual edge details are nonpublic derived geometry.
        detail = [{"version": version, "u": u, "v": v, "length_m": length,
                   "x1": net.nodes[u, 0], "y1": net.nodes[u, 1],
                   "x2": net.nodes[v, 0], "y2": net.nodes[v, 1]}
                  for u, v, length in net.bridge_links(10)]
        private = root / "outputs/private"
        private.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(detail).to_csv(private / f"bridges_{version}.csv", index=False)

    for bridge_m in BRIDGES:
        for older, newer in [("osm", "union"), ("official", "union"), ("union", "lowstress")]:
            for kind in KINDS:
                for cutoff in CUTOFFS:
                    old, new = masks[(older, bridge_m, kind, cutoff)], masks[(newer, bridge_m, kind, cutoff)]
                    tr = transition_summary(old, new, pop)
                    transitions.append(dict(older=older, newer=newer, bridge_m=bridge_m,
                                            kind=kind, cutoff=cutoff, **tr))
                    # Independently rebuilt 10m correction graphs need not nest;
                    # primary (no invented gap edge) source networks must nest.
                    if bridge_m == 0 and tr["lost_pop"] > 1e-6:
                        raise AssertionError(f"source superset lost accessible origins: {older}->{newer}, {kind}, {cutoff}, {tr}")
                    if bridge_m == 0 and cutoff == 2500:
                        prefix = f"access_gap_{newer}_minus_{older}_{kind}_c2500"
                        record(prefix + "_pop", tr["newly_reached_pop"], "신규 도달만; 전이표에 순증/이탈 별도 표시", path=numbers)
                        record(prefix + "_net_pop", tr["net_change_pop"], "총 도달 순증", path=numbers)
                        record(prefix + "_lost_pop", tr["lost_pop"], "모형 도달 집합 이탈, 실제 주민 피해 아님", path=numbers)
        for version in VERSIONS:
            for kind in KINDS:
                tr = transition_summary(masks[(version, 0., kind, 2500.)], masks[(version, 10., kind, 2500.)], pop)
                if tr["lost_pop"] > 1e-6:
                    raise AssertionError("adding fixed bridge edges reduced accessibility")
                if bridge_m == 0:
                    transitions.append(dict(older=version + ":no_bridge", newer=version + ":bridge10",
                                            bridge_m=10., kind=kind, cutoff=2500., **tr))
    output_dir = root / "outputs"
    pd.concat(all_rows, ignore_index=True).to_csv(output_dir / "access_scenarios.csv", index=False)
    pd.DataFrame(transitions).to_csv(output_dir / "access_transitions.csv", index=False)
    pd.DataFrame(bridge_rows).to_csv(output_dir / "bridge_scenarios.csv", index=False)
    record("access_model_version", "audit-v2", NOTE, path=numbers)
    record("access_correction_state", correction_note, "sample coverage remains partial", path=numbers)
    out = pd.concat(rows, ignore_index=True)
    return out[["dong", "district", "pop", "version", "kind", "cutoff",
                "pop_reaching", "pop_detached", "pop_attached_unreached"]]


def print_dry_run(repo_root, prepared_inputs=False):
    missing = missing_inputs(repo_root, prepared_inputs)
    print("missing inputs:", missing)
    return missing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=config.ROOT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--prepared-inputs", action="store_true",
                        help="Reuse committed derived origins/destinations; not raw population reproduction")
    parser.add_argument("--corrections", type=Path)
    args = parser.parse_args()
    missing = print_dry_run(args.repo_root, args.prepared_inputs)
    if args.dry_run:
        return
    if missing:
        raise SystemExit(f"missing inputs: {missing}")
    out = run_analysis(args.repo_root, args.corrections, args.prepared_inputs)
    out.to_csv(args.repo_root / "outputs/access_by_dong.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
