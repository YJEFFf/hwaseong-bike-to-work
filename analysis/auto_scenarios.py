"""Automatic hypotheses, deliberately separate from human judgment corrections.

Exact sampled line extents may be withheld/restored. Polygon evidence never
creates a route. Recorded OSM road features retain direction/layer/node refs.
Straight gap links are exposed only as explicitly hypothetical interventions.
"""
from dataclasses import replace
import math

import numpy as np
import pandas as pd
import shapely
from shapely.geometry import Point
from shapely.ops import substring
from scipy.sparse.csgraph import dijkstra

STATES = {"supported", "possible", "parallel", "unresolved", "restricted"}
GAP_TYPES = {"candidate", "island_gap"}


def intervention_type(state):
    return {"supported": "자료 연결 보정 가정", "possible": "연결 여부 불확실 시나리오",
            "unresolved": "연결 개선 가정"}.get(state, "사업 후보 제외")


def read_auto_table(path):
    rows = pd.read_csv(path, keep_default_na=False)
    required = {"pid", "geometry_group", "sample_type", "auto_class", "target_wkt", "judge_id",
                "osm_road_path_ids", "district"}
    if not required <= set(rows) or rows.pid.duplicated().any():
        raise ValueError("invalid automatic table columns/duplicate IDs")
    if not rows.judge_id.eq("AUTO_VECTOR_V1").all() or not rows.auto_class.isin(STATES).all():
        raise ValueError("expected AUTO_VECTOR_V1, not human judgments")
    if rows.groupby("geometry_group").auto_class.nunique().gt(1).any():
        raise ValueError("duplicate geometry has inconsistent automatic decisions")
    rows = rows.assign(_order=rows.sample_type.ne("candidate")).sort_values(["_order", "pid"])
    rows = rows.drop_duplicates("geometry_group").drop(columns="_order").reset_index(drop=True)
    geoms = shapely.from_wkt(rows.target_wkt.to_numpy())
    if any(g.is_empty or not g.is_valid for g in geoms):
        raise ValueError("invalid automatic target geometry")
    rows["geometry"] = geoms
    return rows


def exact_interval(feature, target, tolerance=.02):
    """Do not remove an entire feature or a nearby parallel line for a sample."""
    if target.geom_type != "LineString":
        return None
    a, b = sorted(feature.geometry.project(Point(p)) for p in (target.coords[0], target.coords[-1]))
    if b-a <= 1e-8:
        return None
    part = substring(feature.geometry, a, b)
    if abs(part.length-target.length) <= tolerance and part.hausdorff_distance(target) <= tolerance:
        return a, b
    return None


def retained_pieces(feature, intervals):
    if not intervals:
        return [feature]
    cursor, result = 0., []
    coords = np.asarray(feature.geometry.coords)[:, :2]
    offsets = np.r_[0., np.cumsum(np.linalg.norm(np.diff(coords, axis=0), axis=1))]
    for start, end in sorted(intervals) + [(feature.geometry.length, feature.geometry.length)]:
        if start > cursor + 1e-8:
            geom = substring(feature.geometry, cursor, start)
            refs = ()
            if len(feature.node_refs) == len(coords):
                xy = np.asarray(geom.coords)[:, :2]
                positions = cursor + np.r_[0., np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))]
                refs_list = []
                for distance in positions:
                    i = int(np.argmin(abs(offsets-distance)))
                    refs_list.append(feature.node_refs[i] if abs(offsets[i]-distance) < 1e-6 else None)
                refs = tuple(refs_list)
            result.append(replace(feature, fid=f"{feature.fid}:auto_retained:{cursor:.8f}:{start:.8f}",
                                  geometry=geom, node_refs=refs))
        cursor = max(cursor, end)
    return result


def osm_id(feature):
    bits = feature.fid.split(":")
    return int(bits[1]) if bits[0] == "osm" and bits[1].isdigit() else None


def compile_scenarios(union_features, road_features, rows):
    """Return nested auto scenarios and a per-target/per-feature operation ledger.

    Unobserved remainder of the city stays in its original source state; this
    small sample does not validate or reclassify the entire network.
    """
    features = list(union_features)
    tree = shapely.STRtree([f.geometry for f in features])
    matches = {}
    for row in rows.itertuples():
        if row.sample_type in GAP_TYPES or row.geometry.geom_type != "LineString":
            continue
        matches[row.pid] = []
        for raw_i in tree.query(row.geometry, predicate="dwithin", distance=.02):
            i = int(raw_i)
            interval = exact_interval(features[i], row.geometry)
            if interval is not None:
                matches[row.pid].append((i, interval))
    baseline_osm = {osm_id(f) for f in features}
    additions = {}
    for feature in road_features:
        way = osm_id(feature)
        if way is not None and way not in baseline_osm:
            additions.setdefault(way, []).append(feature)
    definitions = {
        "auto_base": {"supported"},
        "auto_expanded": {"supported", "possible"},
        "auto_unknown_restored": {"supported", "possible", "unresolved"},
    }
    scenarios, ledger = {"source_union": features}, []
    for name, included in definitions.items():
        removals, extras = {}, {}
        for row in rows.itertuples():
            base = {"scenario": name, "pid": row.pid, "geometry_group": row.geometry_group,
                    "sample_type": row.sample_type, "auto_class": row.auto_class}
            if row.geometry.geom_type != "LineString":
                ledger.append({**base, "action": "polygon_observation_only", "fid": "", "length_m": 0.})
                continue
            if row.sample_type not in GAP_TYPES:
                for i, interval in matches[row.pid]:
                    if row.auto_class not in included:
                        removals.setdefault(i, []).append(interval)
                    ledger.append({**base, "action": "keep_sample" if row.auto_class in included else "withhold_exact_sample",
                                   "fid": features[i].fid, "length_m": interval[1]-interval[0]})
                if not matches[row.pid]:
                    ledger.append({**base, "action": "no_exact_source_match_no_edit", "fid": "", "length_m": 0.})
            else:
                ledger.append({**base, "action": "gap_observation_no_straight_route_inserted", "fid": "", "length_m": 0.})
            if name != "auto_base" and row.auto_class in included:
                for way in filter(None, str(row.osm_road_path_ids).split(";")):
                    for feature in additions.get(int(way), []):
                        extras[feature.fid] = feature
                        ledger.append({**base, "action": "add_recorded_osm_way_full_extent", "fid": feature.fid,
                                       "length_m": feature.geometry.length})
        result = [part for i, f in enumerate(features) for part in retained_pieces(f, removals.get(i, []))]
        # Withheld intervals can also occur in a road snapshot added as evidence.
        # Apply exact matching again, never a broad buffer deletion.
        for feature in extras.values():
            intervals = []
            for row in rows.itertuples():
                if row.sample_type not in GAP_TYPES and row.auto_class not in included:
                    hit = exact_interval(feature, row.geometry)
                    if hit is not None:
                        intervals.append(hit)
            result.extend(retained_pieces(feature, intervals))
        scenarios[name] = result
    return scenarios, pd.DataFrame(ledger)


def gap_rows(rows):
    return rows[rows.sample_type.isin(GAP_TYPES)].copy()


def fixed_anchor_points(rows):
    points = [p for row in gap_rows(rows).itertuples() for p in (row.geometry.coords[0], row.geometry.coords[-1])]
    return np.array(sorted(set(points)), dtype=float).reshape(-1, 2)


def map_gap_links(network, rows, eligible_ids=None, reject_existing=False):
    """Hypothetical bidirectional links, never current-road assertions.

    Only existing ground-level physical nodes are linked. Virtual links do not
    create mid-chord junctions or new lateral access for nearby population.
    """
    links, audit = {}, []
    for row in gap_rows(rows).itertuples():
        geom = row.geometry
        endpoints = np.array([geom.coords[0], geom.coords[-1]])
        mapped = network.endpoint_indices(endpoints, 1.5, ["0", "0"])
        a, b = map(int, mapped)
        reason = "eligible_hypothetical_link"
        if row.auto_class in {"parallel", "restricted"}:
            reason = "excluded_parallel_or_restriction"
        elif eligible_ids is not None and row.pid not in eligible_ids:
            reason = "outside_frozen_candidate_universe"
        elif min(a, b) < 0:
            reason = "unmapped_ground_endpoint"
        elif a == b:
            reason = "same_existing_node"
        elif reject_existing:
            threshold = max(3*geom.length, geom.length+200.)
            outward = dijkstra(network.physical_graph, directed=True, indices=a, limit=threshold)[b]
            backward = dijkstra(network.physical_graph, directed=True, indices=b, limit=threshold)[a]
            if max(outward, backward) <= threshold:
                reason = "short_bidirectional_route_already_recorded"
        length = float(geom.length)
        if min(a, b) >= 0:
            length += float(np.linalg.norm(network.nodes[[a, b]]-endpoints, axis=1).sum())
        if reason == "eligible_hypothetical_link":
            links[row.pid] = (a, b, length)
        audit.append({"pid": row.pid, "district": row.district, "auto_class": row.auto_class,
                      "intervention_type": intervention_type(row.auto_class),
                      "reason": reason, "model_length_m": length, "target_length_m": geom.length,
                      "x1": endpoints[0, 0], "y1": endpoints[0, 1], "x2": endpoints[1, 0], "y2": endpoints[1, 1]})
    return links, pd.DataFrame(audit)
