"""Directed, layer-aware routing with explicit access to line interiors.

Each origin/destination may approach the closest point on *every* eligible
source line within access_max. Connectors retain their Euclidean cost. Origins
have outgoing connectors only, destinations incoming only: neither can act as
a shortcut between parallel roads. Adding a line preserves previous options.

Straight access connectors and same-level geometric junctions remain modelling
assumptions, not evidence that an entrance/crossing is legally usable. Elevated
lines do not accept lateral access. Explicit OSM shared nodes connect levels.
"""
from dataclasses import dataclass, field, replace
import hashlib
import json
from pathlib import Path

import numpy as np
import shapely
from pyproj import Transformer
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point, shape

from analysis.osm_filter import is_bike_facility, is_low_stress


def geometry_hash(geometry):
    return hashlib.sha256(geometry.wkb).hexdigest()


def normalize_features(features):
    """Split only non-simple paths into ordered primitive segments.

    Preserve direction, layer and available endpoint refs. Do not discard the
    entire road or unary-union away its direction. New IDs retain parent IDs.
    """
    result = []
    for feature in features:
        geom = feature.geometry
        if geom.geom_type != "LineString":
            raise ValueError("routing input must be LineString")
        if geom.is_simple:
            result.append(feature)
            continue
        coords = list(geom.coords)
        for i, (a, b) in enumerate(zip(coords[:-1], coords[1:])):
            line = LineString([a, b])
            if line.length <= 0:
                continue
            refs = feature.node_refs[i:i+2] if len(feature.node_refs) == len(coords) else ()
            result.append(replace(feature, fid=f"{feature.fid}:simplepart:{i}",
                                  geometry=line, node_refs=refs))
    return result


@dataclass(frozen=True)
class RouteFeature:
    fid: str
    geometry: LineString
    direction: int = 0  # 0 both, 1 geometry order, -1 reverse
    level: str = "0"
    lateral_access: bool = True
    source: str = "synthetic"
    tags: dict = field(default_factory=dict, compare=False)
    node_refs: tuple = ()


def bicycle_allowed(tags):
    """Operational routing permission, distinct from a facility inventory."""
    bike = tags.get("bicycle")
    if bike in {"no", "dismount", "private", "use_sidepath"}:
        return False
    if tags.get("highway") in {"construction", "proposed", "steps"}:
        return False
    if tags.get("construction") or tags.get("proposed"):
        return False
    if tags.get("access") in {"no", "private"} and bike not in {"yes", "designated", "permissive"}:
        return False
    return True


def bicycle_direction(tags):
    value = tags.get("oneway:bicycle", tags.get("oneway", ""))
    if value in {"no", "0", "false"}:
        return 0
    if "oneway:bicycle" not in tags and any(
        tags.get(k) in {"opposite", "opposite_lane", "opposite_track"}
        for k in ("cycleway", "cycleway:left", "cycleway:right", "cycleway:both")
    ):
        return 0
    if value == "-1":
        return -1
    return 1 if value in {"yes", "1", "true"} or (
        not value and tags.get("junction") == "roundabout"
    ) else 0


def load_features(repo_root, version, corrections=None):
    """Keep source IDs and routing attributes; never infer a line from a polygon."""
    root = Path(repo_root)
    if version not in {"osm", "official", "union", "lowstress"}:
        raise ValueError(f"unknown network: {version}")
    result = []
    if version in {"official", "union", "lowstress"}:
        fc = json.loads((root / "data/raw/화성_자전거노선_5186.geojson").read_text())
        for i, feat in enumerate(fc["features"]):
            geom, props = shape(feat["geometry"]), feat.get("properties", {})
            parts = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
            for part, line in enumerate(parts):
                if line.geom_type != "LineString":
                    raise ValueError("official routing input must be linear")
                result.append(RouteFeature(
                    f"official:{feat.get('id', i)}:{part}", line,
                    source="official", tags=dict(props),
                ))
    if version in {"osm", "union", "lowstress"}:
        tf = Transformer.from_crs(4326, 5186, always_xy=True)
        sources = [("data/raw/osm_hwaseong_bike_20260914.json", is_bike_facility)]
        if version == "lowstress":
            sources.append(("data/raw/osm_roads/roads.json", is_low_stress))
        seen = set()
        for path, inventory_filter in sources:
            data = json.loads((root / path).read_text())
            for el in data.get("elements", []):
                tags = el.get("tags", {}) or {}
                if el.get("type") != "way" or not inventory_filter(tags) or not bicycle_allowed(tags):
                    continue
                points = el.get("geometry") or []
                if len(points) < 2:
                    continue
                coords = [tf.transform(p["lon"], p["lat"]) for p in points]
                geom = LineString(coords)
                # Different snapshots of the same way must not silently replace
                # a previously included line when the road background is added.
                way_id = el.get("id", f"anonymous-{geometry_hash(geom)[:12]}")
                tag_text = json.dumps(tags, sort_keys=True)
                signature = (way_id, geometry_hash(geom), tag_text)
                if signature in seen:
                    continue
                seen.add(signature)
                bridge = tags.get("bridge") not in {None, "no", "false", "0"}
                tunnel = tags.get("tunnel") not in {None, "no", "false", "0"}
                layer = str(tags.get("layer", "1" if bridge else "-1" if tunnel else "0"))
                level = f"{layer}:{'bridge' if bridge else 'tunnel' if tunnel else 'surface'}"
                if layer == "0" and not bridge and not tunnel:
                    level = "0"
                result.append(RouteFeature(
                    f"osm:{way_id}:{geometry_hash(geom)[:12]}:{hashlib.sha256(tag_text.encode()).hexdigest()[:8]}", geom,
                    direction=bicycle_direction(tags), level=level,
                    lateral_access=not (bridge or tunnel) and layer == "0",
                    source="osm", tags=tags, node_refs=tuple(el.get("nodes", ())),
                ))
    result = normalize_features(result)
    if corrections is not None:
        from analysis.judgments import apply_corrections
        result, _ = apply_corrections(result, corrections)
    return result


def _xy(values):
    out = np.asarray(values, dtype=float)
    if out.size == 0:
        return np.empty((0, 2))
    if out.ndim != 2 or out.shape[1] < 2 or not np.isfinite(out[:, :2]).all():
        raise ValueError("coordinates must be a finite N x 2 array")
    return out[:, :2]


def _sparse(edges, n):
    if not edges:
        return coo_matrix((n, n), dtype=float).tocsr()
    pairs = np.array(list(edges), dtype=np.int64)
    return coo_matrix((list(edges.values()), (pairs[:, 0], pairs[:, 1])), shape=(n, n)).tocsr()


def _put(edges, i, j, cost):
    if i != j:
        edges[(i, j)] = min(edges.get((i, j), np.inf), float(cost))


@dataclass
class AccessNetwork:
    graph: object
    physical_graph: object
    nodes: np.ndarray
    levels: tuple
    origin_nodes: np.ndarray
    destination_nodes: np.ndarray
    detached: np.ndarray
    features: tuple
    connector_count: int
    junction_policy: str

    def distances(self, destination_mask=None, limit=np.inf, graph=None):
        graph = self.graph if graph is None else graph
        targets = self.destination_nodes
        if destination_mask is not None:
            targets = targets[np.asarray(destination_mask, dtype=bool)]
        if not len(targets):
            return np.full(graph.shape[0], np.inf)
        # Incoming-only target edges and outgoing-only origin edges avoid
        # fictitious transfers through a residence or school.
        return dijkstra(graph.T.tocsr(), directed=True, indices=targets,
                        min_only=True, limit=limit)

    def approach_distances(self, destination_mask=None, limit=np.inf, graph=None):
        return self.distances(destination_mask, limit, graph)[self.origin_nodes]

    def with_links(self, links, graph=None):
        """Add the complete proposed bundle. Endpoints are physical node IDs."""
        original = self.graph if graph is None else graph
        updated = original.tolil(copy=True)
        for u, v, length in links:
            if min(u, v) < 0 or max(u, v) >= len(self.nodes):
                raise ValueError("link endpoints must refer to physical nodes")
            if not np.isfinite(length) or length <= 0:
                raise ValueError("link length must be positive and finite")
            for a, b in ((int(u), int(v)), (int(v), int(u))):
                value = float(updated[a, b])
                updated[a, b] = min(value, length) if value > 0 else length
        return updated.tocsr()

    def endpoint_indices(self, xy, tolerance, levels=None):
        """Reject ambiguous stacked nodes rather than choosing one by row order."""
        points = _xy(xy)
        result = np.full(len(points), -1, dtype=int)
        if not len(self.nodes):
            return result
        tree = cKDTree(self.nodes)
        for i, point in enumerate(points):
            hits = tree.query_ball_point(point, tolerance)
            if levels is not None:
                hits = [j for j in hits if self.levels[j] == str(levels[i])]
            elif len({self.levels[j] for j in hits}) > 1:
                continue
            if hits:
                result[i] = min(hits, key=lambda j: (float(np.linalg.norm(self.nodes[j] - point)), j))
        return result

    def bridge_links(self, tolerance):
        """Unverified same-level endpoint corrections, returned for explicit scenarios."""
        if tolerance <= 0 or not len(self.nodes):
            return []
        weak = self.physical_graph.maximum(self.physical_graph.T)
        _, component = connected_components(weak, directed=False)
        degree = np.diff(weak.indptr)
        tree = cKDTree(self.nodes)
        links = {}
        for u in np.flatnonzero(degree == 1):
            hits = [v for v in tree.query_ball_point(self.nodes[u], tolerance)
                    if component[v] != component[u] and self.levels[v] == self.levels[u]]
            if hits:
                v = min(hits, key=lambda j: (float(np.linalg.norm(self.nodes[u] - self.nodes[j])), j))
                length = float(np.linalg.norm(self.nodes[u] - self.nodes[v]))
                if length > 0:
                    links[tuple(sorted((int(u), int(v))))] = length
        return [(u, v, length) for (u, v), length in sorted(links.items())]


def build_access_network(features, origins_xy, destinations_xy, *, snap=1.0,
                         access_max=150.0, junction_policy="same_level"):
    """Split source lines at access projections and allowed junctions.

    Every source line contributes its existing access choices independently;
    no fixed-k truncation or nearest-new-line replacement is performed.
    ``same_level`` nodes geometric intersections only within an equal level;
    ``shared_vertices`` is a conservative sensitivity alternative. Neither
    creates bridges across a positive gap.
    """
    if snap <= 0 or access_max < 0 or junction_policy not in {"same_level", "shared_vertices"}:
        raise ValueError("invalid snap/access/junction settings")
    origins, destinations = _xy(origins_xy), _xy(destinations_xy)
    features = tuple(f for f in normalize_features(features) if not f.geometry.is_empty and f.geometry.length > 0)
    ids = [f.fid for f in features]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate route feature IDs; preserve snapshot provenance")
    geoms = np.asarray([f.geometry for f in features], dtype=object)
    for f in features:
        if f.geometry.geom_type != "LineString" or not f.geometry.is_simple:
            raise ValueError(f"routing requires simple LineString: {f.fid}")
        if f.direction not in {-1, 0, 1}:
            raise ValueError(f"invalid direction: {f.fid}")
    positions = []
    ref_at = []
    # Keep cumulative positions, not positions rounded to the graph grid.
    for f in features:
        coords = np.asarray(f.geometry.coords)[:, :2]
        offsets = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(coords, axis=0), axis=1))]
        positions.append(list(offsets))
        refs = {off: ref for off, ref in zip(offsets, f.node_refs) if ref is not None} if len(f.node_refs) == len(offsets) else {}
        ref_at.append(refs)

    if len(features) and junction_policy == "same_level":
        tree = shapely.STRtree(geoms)
        left, right = tree.query(geoms, predicate="intersects")
        for i, j in zip(left, right):
            if i >= j or features[i].level != features[j].level:
                continue
            intersection = geoms[i].intersection(geoms[j])
            for part in shapely.get_parts(intersection):
                pts = [part] if part.geom_type == "Point" else (
                    [Point(part.coords[0]), Point(part.coords[-1])]
                    if part.geom_type == "LineString" else []
                )
                for p in pts:
                    positions[i].append(float(geoms[i].project(p)))
                    positions[j].append(float(geoms[j].project(p)))

    attachments = []
    all_points = np.concatenate([origins, destinations])
    if len(features) and len(all_points):
        active = np.array([i for i, f in enumerate(features) if f.lateral_access], dtype=int)
        if len(active):
            tree = shapely.STRtree(geoms[active])
            point_idx, line_idx = tree.query(shapely.points(all_points), predicate="dwithin", distance=access_max)
            line_idx = active[line_idx]
            distances = shapely.line_locate_point(geoms[line_idx], shapely.points(all_points[point_idx]))
            feet = shapely.line_interpolate_point(geoms[line_idx], distances)
            costs = shapely.distance(feet, shapely.points(all_points[point_idx]))
            for p, line, offset, cost in zip(point_idx, line_idx, distances, costs):
                positions[line].append(float(offset))
                attachments.append((int(p), int(line), float(offset), float(cost)))

    keys, coords, levels, edges = {}, [], [], {}
    line_nodes = []
    def node(p, level):
        spatial = (level, round(p.x / snap), round(p.y / snap))
        key = spatial
        if key not in keys:
            keys[key] = len(coords)
            coords.append((spatial[1] * snap, spatial[2] * snap))
            levels.append(level)
        return keys[key]

    for f, offsets in zip(features, positions):
        offsets = sorted(set(offsets))
        mapping = {offset: node(f.geometry.interpolate(offset), f.level) for offset in offsets}
        line_nodes.append(mapping)
        for start, end in zip(offsets[:-1], offsets[1:]):
            u, v, length = mapping[start], mapping[end], end - start
            if f.direction >= 0:
                _put(edges, u, v, length)
            if f.direction <= 0:
                _put(edges, v, u, length)
    # Union explicit shared nodes after spatial noding. Additional features may
    # merge existing nodes, but never revoke a previously available connection.
    parent = list(range(len(coords)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    refs_seen = {}
    for refs, mapping in zip(ref_at, line_nodes):
        for offset, ref in refs.items():
            idx = mapping[offset]
            if ref in refs_seen:
                parent[find(idx)] = find(refs_seen[ref])
            else:
                refs_seen[ref] = idx
    roots = sorted({find(i) for i in range(len(coords))}, key=lambda i: (levels[i], *coords[i]))
    index_of = {r: i for i, r in enumerate(roots)}
    remap = {i: index_of[find(i)] for i in range(len(coords))}
    merged_edges = {}
    for (u, v), length in edges.items():
        _put(merged_edges, remap[u], remap[v], length)
    edges = merged_edges
    line_nodes = [{offset: remap[idx] for offset, idx in mapping.items()} for mapping in line_nodes]
    coords, levels = [coords[i] for i in roots], [levels[i] for i in roots]
    n_physical = len(coords)
    physical = _sparse(edges, n_physical)
    origin_nodes = np.arange(n_physical, n_physical + len(origins))
    dest_nodes = np.arange(n_physical + len(origins), n_physical + len(all_points))
    detached = np.ones(len(origins), dtype=bool)
    for p, line, offset, cost in attachments:
        physical_node = line_nodes[line][offset]
        auxiliary_node = n_physical + p
        if p < len(origins):
            _put(edges, auxiliary_node, physical_node, cost)
            detached[p] = False
        else:
            _put(edges, physical_node, auxiliary_node, cost)
    graph = _sparse(edges, n_physical + len(all_points))
    return AccessNetwork(graph, physical, np.asarray(coords, dtype=float).reshape(-1, 2),
                         tuple(levels), origin_nodes, dest_nodes, detached, features,
                         len(attachments), junction_policy)


def transition_summary(old_mask, new_mask, population):
    old, new, pop = np.asarray(old_mask, bool), np.asarray(new_mask, bool), np.asarray(population, float)
    if old.shape != new.shape or old.shape != pop.shape:
        raise ValueError("transition masks/population must share one origin order")
    return {
        "retained_pop": float(pop[old & new].sum()),
        "newly_reached_pop": float(pop[~old & new].sum()),
        "lost_pop": float(pop[old & ~new].sum()),
        "still_unreached_pop": float(pop[~old & ~new].sum()),
        "net_change_pop": float(pop[new].sum() - pop[old].sum()),
    }
