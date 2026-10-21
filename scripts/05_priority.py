"""Audit-v2 priorities: equal length budget, fixed comparison group, exact bundles.

Candidate straight links are counterfactuals, not surveyed construction designs.
Single-link scores rank a heuristic; all selected links are then inserted together
and shortest paths recomputed. No claim of a globally optimal investment plan.
"""
import argparse
import json
import sys
from itertools import product
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import shapely
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree
from shapely.ops import unary_union
from analysis import config
from analysis.admin import polygons_from_geojson
from analysis.candidates import bottom_share_mask, dangling_candidates, stable_origin_rank
from analysis.numbers import record
from analysis.routing import build_access_network, load_features
from analysis.priority import select_length_budget, bundle_gain
from analysis.figures import fig3_priority_links

ADMIN_GEOJSON_REL = "data/raw/admin/hwaseong_hjd_20260701.geojson"
REGION_BUFFER_M = 1500.
RULES = ("short", "reach", "bottom20")
NOTE = "audit-v2: 동일 연장 상한(동일 공사비 아님), 고정 취약집단, 묶음 동시 추가; 판독 전 잠정"


def region_polygons(repo_root, dong_names):
    dongs = polygons_from_geojson(Path(repo_root) / ADMIN_GEOJSON_REL)
    city = unary_union(list(dongs.values()))
    if len(dong_names) != 2 or len(set(dong_names)) != 2:
        raise ValueError("two distinct case-study districts required")
    return {i: dongs[name].buffer(REGION_BUFFER_M).intersection(city)
            for i, name in enumerate(dong_names, 1)}


def run(repo_root, dong_names, corrections=None, quick=False):
    root = Path(repo_root)
    origins = pd.read_csv(root / "outputs/origins_5186.csv")
    dests = pd.read_csv(root / "outputs/destinations_5186.csv")
    xy, pop = origins[["x", "y"]].to_numpy(float), origins["pop"].to_numpy(float)
    if not np.isfinite(pop).all() or (pop < 0).any() or origins[["x", "y"]].duplicated().any():
        raise ValueError("invalid/duplicate population inputs")
    school_xy = dests[dests.kind.isin({"초등학교", "중학교", "고등학교"})][["x", "y"]].to_numpy(float)
    if not len(school_xy):
        raise ValueError("school destinations missing; cannot rank access disadvantage")
    polygons = region_polygons(root, dong_names)
    inside = {r: shapely.intersects_xy(poly, xy[:, 0], xy[:, 1]) for r, poly in polygons.items()}
    features = {v: load_features(root, v, corrections) for v in ("union", "lowstress")}
    base = build_access_network(features["union"], xy, school_xy)
    base_dist = base.approach_distances()
    euclid = cKDTree(school_xy).query(xy)[0]
    stable = stable_origin_rank(xy)
    vulnerable, pool_rows = {}, []
    for r, mask in inside.items():
        pool = np.flatnonzero(mask & ~base.detached)
        selected = np.zeros(len(pop), bool)
        selected[pool] = bottom_share_mask(base_dist[pool], pop[pool], .2,
                                          tiebreak_desc=euclid[pool], stable_id=stable[pool])
        vulnerable[r] = selected  # frozen across ALL scenario settings
        attached_pop = float(pop[pool].sum())
        pool_rows.append(dict(region=r, dong=dong_names[r-1], attached_pop=attached_pop,
                              detached_pop=float(pop[mask & base.detached].sum()),
                              bottom_group_pop=float(pop[selected].sum()),
                              bottom_group_share=float(pop[selected].sum()/attached_pop) if attached_pop else np.nan))
    weak = base.physical_graph.maximum(base.physical_graph.T)
    candidates = dangling_candidates(weak, base.nodes, polygons, 100.)
    if len(candidates):
        u = base.endpoint_indices(candidates[["x1", "y1"]], 1.01)
        v = base.endpoint_indices(candidates[["x2", "y2"]], 1.01)
        ok = np.array([a >= 0 and b >= 0 and base.levels[a] == base.levels[b]
                       for a, b in zip(u, v)]) & (candidates.gap_m.to_numpy() > 0)
        candidates = candidates.loc[ok].copy()
        candidates["level"] = [base.levels[a] for a in u[ok]]
    else:
        candidates["level"] = pd.Series(dtype=str)
    # A common upper bound, fixed before scoring: sum of the 20 shortest
    # candidate lengths per case-study region. Report actual used lengths too.
    budgets = {r: float(candidates[candidates.region.eq(r)].nsmallest(20, "gap_m").gap_m.sum())
               for r in polygons}
    settings = [(1., 100., "union", 2500., 0.)] if quick else [
        (s, g, v, c, b) for s, v, b, g, c in product(
            [1., 5., 10.], ["union", "lowstress"], [0., 10.], [50., 100.], [2000., 2500., 3000.])]
    rows, selection_rows, baseline_rows = [], [], []
    nets, scores_cache = {}, {}
    for snap, max_gap, version, cutoff, bridge in settings:
        key = (snap, version)
        if key not in nets:
            nets.clear()  # bounded memory: full city graphs can be large
            nets[key] = base if key == (1., "union") else build_access_network(
                features[version], xy, school_xy, snap=snap)
            scores_cache.clear()
        net = nets[key]
        graph = net.with_links(net.bridge_links(bridge)) if bridge else net.graph
        cache_key = (key, bridge)
        if cache_key not in scores_cache:
            scores_cache.clear()
            d0 = net.distances(graph=graph)
            u = net.endpoint_indices(candidates[["x1", "y1"]], snap + 1., candidates.level)
            v = net.endpoint_indices(candidates[["x2", "y2"]], snap + 1., candidates.level)
            alternatives, links = {}, {}
            for index, c in enumerate(candidates.itertuples()):
                a, b = int(u[index]), int(v[index])
                if min(a, b) < 0 or a == b:
                    continue
                links[c.cand_id] = (a, b, float(c.gap_m))
                # directed distances are origin -> endpoint, hence reverse graph.
                du = dijkstra(graph.T.tocsr(), directed=True, indices=a)[net.origin_nodes]
                dv = dijkstra(graph.T.tocsr(), directed=True, indices=b)[net.origin_nodes]
                alternatives[c.cand_id] = np.minimum(
                    d0[net.origin_nodes], np.minimum(du+c.gap_m+d0[b], dv+c.gap_m+d0[a]))
            scores_cache[cache_key] = (d0[net.origin_nodes], alternatives, links)
        before, alternatives, links = scores_cache[cache_key]
        sid = f"s{snap:g}_g{max_gap:g}_{version}_c{cutoff:g}_b{bridge:g}"
        for r, region_mask in inside.items():
            subset = candidates[candidates.region.eq(r) & candidates.gap_m.le(max_gap)]
            ids = [int(c) for c in subset.cand_id if c in links]
            lengths = {int(c.cand_id): float(c.gap_m) for c in subset.itertuples()}
            reach_scores, bottom_scores = {}, {}
            for c in ids:
                gained = (before > cutoff) & (alternatives[c] <= cutoff) & region_mask
                reach_scores[c] = float(pop[gained].sum())
                bottom_scores[c] = float(pop[gained & vulnerable[r]].sum())
            for rule in RULES:
                scores = reach_scores if rule == "reach" else bottom_scores
                picked, used = select_length_budget(ids, lengths, scores, budgets[r], rule)
                gained = bundle_gain(net, [links[c] for c in picked], cutoff, graph=graph)
                row = dict(scenario=sid, region=r, dong=dong_names[r-1], rule=rule,
                           snap_m=snap, max_gap_m=max_gap, network=version, cutoff_m=cutoff, bridge_m=bridge,
                           length_budget_m=budgets[r], used_length_m=used, selected_n=len(picked),
                           gained_pop=float(pop[gained & region_mask].sum()),
                           bottom_group_gained_pop=float(pop[gained & vulnerable[r]].sum()),
                           correction_state="partial_sample_only" if corrections else "unjudged")
                rows.append(row)
                for c in picked:
                    selection_rows.append(dict(scenario=sid, region=r, rule=rule, cand_id=c))
                if (snap, max_gap, version, cutoff, bridge) == (1., 100., "union", 2500., 0.):
                    baseline_rows.append(row)
    output = root / "outputs"
    private = output / "private"
    private.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(private / "priority_candidate_universe.csv", index=False)
    selected_df = pd.DataFrame(selection_rows, columns=["scenario", "region", "rule", "cand_id"])
    selected_df.to_csv(private / "priority_selections.csv", index=False)
    result = pd.DataFrame(rows)
    result.to_csv(output / "priority_scenarios.csv", index=False)
    pd.DataFrame(pool_rows).to_csv(output / "priority_fixed_group.csv", index=False)
    primary = pd.DataFrame(baseline_rows)
    primary.to_csv(output / "priority_summary.csv", index=False)
    # Inclusion is a frequency in this finite assumption grid, not probability.
    priority = candidates.copy()
    for rule in RULES:
        chosen = selected_df[selected_df.rule.eq(rule)]
        counts = chosen.groupby("cand_id").size()
        priority["inclusion_" + rule] = priority.cand_id.map(counts).fillna(0) / len(settings)
    priority.to_csv(private / "priority_links.csv", index=False)
    fig3_priority_links([f.geometry for f in features["union"]], polygons, priority,
                        output / "figures/fig3_priority_links.png")
    manifest = dict(model="audit-v2", scenario_count=len(settings), settings=[list(s) for s in settings],
                    baseline=[1., 100., "union", 2500., 0.], comparator="common_length_cap_not_equal_cost",
                    fixed_group="unbridged_union_snap1_attached_population_worst20_whole_cells",
                    selection="single_edge_score_heuristic_then_exact_bundle_reroute",
                    candidates="same_level_dangling_endpoint_pairs_only_not_all_possible_projects",
                    regions=dong_names, human_state="partial_sample_only" if corrections else "unjudged")
    (output / "priority_method.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n")
    for r in polygons:
        group = primary[primary.region.eq(r)].set_index("rule")
        for rule in RULES:
            record(f"priority_v2_r{r}_{rule}_bottom_gain", float(group.loc[rule, "bottom_group_gained_pop"]), NOTE, path=output/"numbers.csv")
        denominator = float(group.loc["bottom20", "bottom_group_gained_pop"])
        numerator = denominator-float(group.loc["short", "bottom_group_gained_pop"])
        if denominator > 0:
            record(f"priority_v2_r{r}_short_loss_share", numerator/denominator, NOTE+"; 음수도 가능", path=output/"numbers.csv")
        record(f"priority_v2_r{r}_length_budget_m", budgets[r], NOTE, path=output/"numbers.csv")
    return primary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", type=Path, default=config.ROOT)
    p.add_argument("--regions", default="향남읍,동탄6동")
    p.add_argument("--corrections", type=Path)
    p.add_argument("--quick", action="store_true", help="baseline only; not robustness evidence")
    a = p.parse_args()
    run(a.repo_root, a.regions.split(","), a.corrections, a.quick)


if __name__ == "__main__":
    main()
