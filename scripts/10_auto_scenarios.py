"""Run automatic-vector access scenarios and sampled-gap priorities, not reports.

Reads prepared population/destinations; this is NOT raw population reproduction.
Raw inputs are unpacked into an isolated run directory after checksum validation.
Original numbers.csv, reports, human sheets and prior outputs are never modified.
"""
import argparse
import hashlib
from importlib.metadata import version
import json
import platform
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree
from analysis.auto_scenarios import read_auto_table, compile_scenarios, fixed_anchor_points, map_gap_links
from analysis.candidates import bottom_share_mask, stable_origin_rank
from analysis.priority import select_length_budget
from analysis.routing import build_access_network, load_features, transition_summary

RAW_SHA = 'e38291117b4ecd54f81ff1c002693111e896de6b14dd3cafe84c795a72cc2790'
KINDS = ('school', 'bus_stop', 'station')
CUTOFFS = (2000., 2500., 3000.)
BUDGETS = (300., 600.)
SCHOOLS = {'초등학교', '중학교', '고등학교'}
AUTO_NAMES = ('auto_base', 'auto_expanded', 'auto_unknown_restored')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare_inputs(root, raw_zip, work):
    if sha(raw_zip) != RAW_SHA:
        raise ValueError('raw ZIP differs from the frozen team release')
    records = []
    with zipfile.ZipFile(raw_zip) as archive:
        for line in archive.read('SHA256SUMS.txt').decode('utf-8-sig').splitlines():
            checksum, name = line.split(maxsplit=1)
            name = name.lstrip('*').replace('\\', '/')
            while name.startswith('./'):
                name = name[2:]
            relative = PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts or not name.startswith('data/raw/'):
                raise ValueError('unsafe ZIP member')
            content = archive.read(name)
            if hashlib.sha256(content).hexdigest() != checksum:
                raise ValueError(f'raw input checksum mismatch: {name}')
            target = work / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and sha(target) != checksum:
                raise ValueError(f'isolated input has changed; choose a fresh work directory: {name}')
            if not target.exists():
                target.write_bytes(content)
            records.append({'path': name, 'sha256': checksum})
    for name in ('origins_5186.csv', 'destinations_5186.csv'):
        source, target = root/'outputs'/name, work/'outputs'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and sha(target) != sha(source):
            raise ValueError(f'prepared input changed: {name}')
        if not target.exists():
            shutil.copyfile(source, target)
        records.append({'path': f'outputs/{name}', 'sha256': sha(source)})
    return records


def prepare_population(work):
    origins = pd.read_csv(work/'outputs/origins_5186.csv')
    dests = pd.read_csv(work/'outputs/destinations_5186.csv')
    if not np.isfinite(origins[['x', 'y', 'pop']].to_numpy(float)).all() or (origins['pop'] < 0).any():
        raise ValueError('invalid population')
    if origins[['x', 'y']].duplicated().any() or origins['pop'].sum() <= 0:
        raise ValueError('duplicate/empty population')
    fc = json.loads((work/'data/raw/admin/hwaseong_hjd_20260701.geojson').read_text(encoding='utf-8'))
    mapping = {}
    for feature in fc['features']:
        props = feature['properties']
        for district in ('동탄', '병점', '효행', '만세'):
            if district+'구' in props.get('sggnm', ''):
                mapping[props['adm_nm'].split()[-1]] = district
    origins['district'] = origins.dong.map(mapping)
    if origins.district.isna().any():
        raise ValueError('unmatched population dong')
    return origins, dests


def summarise_access(name, net, graph, origins, dests):
    pop = origins['pop'].to_numpy(float)
    n = len(origins)
    rows, masks, distances = [], {}, {}
    for kind in KINDS:
        dmask = dests.kind.isin(SCHOOLS) if kind == 'school' else dests.kind.eq(kind)
        if not dmask.any():
            raise ValueError(f'missing destinations: {kind}')
        distance = net.approach_distances(dmask, max(CUTOFFS), graph)[:n]
        distances[kind] = distance
        for cutoff in CUTOFFS:
            reached = distance <= cutoff
            masks[(kind, cutoff)] = reached
            scopes = [('city', '화성시', np.ones(n, bool))]
            scopes.extend(('district', k, origins.district.eq(k).to_numpy()) for k in sorted(origins.district.unique()))
            scopes.extend(('dong', k, origins.dong.eq(k).to_numpy()) for k in sorted(origins.dong.unique()))
            for level, area, inside in scopes:
                denom = float(pop[inside].sum())
                rows.append(dict(scenario=name, kind=kind, cutoff_m=cutoff, level=level, area=area,
                    population=denom, reaching_pop=float(pop[inside & reached].sum()),
                    reaching_share=float(pop[inside & reached].sum()/denom) if denom else np.nan,
                    detached_pop=float(pop[inside & net.detached[:n]].sum()),
                    attached_unreached_pop=float(pop[inside & ~net.detached[:n] & ~reached].sum())))
    return rows, masks, distances


def rank_candidates(name, net, dests, pop, fixed_group, links, lengths, candidate_info):
    """Single-edge heuristic ranking, then reroute the whole selected bundle."""
    n = len(pop)
    target_mask = dests.kind.isin(SCHOOLS)
    d0 = net.distances(target_mask, limit=max(CUTOFFS))
    before = d0[net.origin_nodes[:n]]
    alternatives = {}
    reverse = net.graph.T.tocsr()
    for pid, (a, b, length) in links.items():
        du = dijkstra(reverse, directed=True, indices=a, limit=max(CUTOFFS))[net.origin_nodes[:n]]
        dv = dijkstra(reverse, directed=True, indices=b, limit=max(CUTOFFS))[net.origin_nodes[:n]]
        alternatives[pid] = np.minimum(before, np.minimum(du+length+d0[b], dv+length+d0[a]))
    rankings, bundles, selections = [], [], []
    for cutoff in CUTOFFS:
        scores, bottom_scores = {}, {}
        for pid, after in alternatives.items():
            gained = (before > cutoff) & (after <= cutoff)
            scores[pid] = float(pop[gained].sum())
            bottom_scores[pid] = float(pop[gained & fixed_group].sum())
            rankings.append(dict(scenario=name, cutoff_m=cutoff, pid=pid,
                district=candidate_info[pid]['district'], auto_class=candidate_info[pid]['auto_class'],
                intervention_type=candidate_info[pid]['intervention_type'],
                length_m=lengths[pid], gained_pop=scores[pid], bottom_group_gained_pop=bottom_scores[pid]))
        for budget in BUDGETS:
            for rule in ('short', 'reach', 'bottom20'):
                used_scores = scores if rule == 'reach' else bottom_scores
                picked, used = select_length_budget(sorted(links), lengths, used_scores, budget, rule)
                graph = net.with_links([links[pid] for pid in picked])
                after = net.approach_distances(target_mask, cutoff, graph)[:n]
                gained = (before > cutoff) & (after <= cutoff)
                if np.any((before <= cutoff) & (after > cutoff)):
                    raise AssertionError('hypothetical bundle revoked reachability')
                row = dict(scenario=name, cutoff_m=cutoff, length_budget_m=budget, rule=rule,
                           eligible_n=len(links), selected_n=len(picked), used_length_m=used,
                           gained_pop=float(pop[gained].sum()),
                           bottom_group_gained_pop=float(pop[gained & fixed_group].sum()))
                bundles.append(row)
                selections.extend({**{k: row[k] for k in ('scenario', 'cutoff_m', 'length_budget_m', 'rule')}, 'pid': pid} for pid in picked)
    return rankings, bundles, selections


def run(root, raw_zip, auto_csv, work, output):
    started = time.monotonic()
    output.mkdir(parents=True, exist_ok=True)
    provenance = prepare_inputs(root, raw_zip, work)
    origins, dests = prepare_population(work)
    auto = read_auto_table(auto_csv)
    if len(auto) != 171:
        raise ValueError('expected 171 deduplicated targets from the original 176 rows')
    anchors = fixed_anchor_points(auto)
    xy = np.vstack([origins[['x', 'y']].to_numpy(float), anchors])
    pop = origins['pop'].to_numpy(float)
    print(f'Inputs: {len(origins)} origins, {len(dests)} destinations, {len(auto)} targets, {len(anchors)} fixed anchors', flush=True)
    union = load_features(work, 'union')
    roads = load_features(work, 'lowstress')
    scenarios, ledger = compile_scenarios(union, roads, auto)
    ledger.to_csv(output/'automatic_operations.csv', index=False, encoding='utf-8-sig')
    auto.drop(columns='geometry').to_csv(output/'targets_171.csv', index=False, encoding='utf-8-sig')
    masks_by_name, access_rows, graph_rows = {}, [], []
    ranking_rows, bundle_rows, selection_rows, candidate_audits = [], [], [], []
    candidate_ids, lengths, candidate_info, fixed_group = None, None, None, None
    for name, features in scenarios.items():
        print(f'Building {name}: {len(features)} source features', flush=True)
        net = build_access_network(features, xy, dests[['x', 'y']].to_numpy(float), snap=1., access_max=150.)
        print(f'Built {name}: {len(net.nodes)} physical nodes', flush=True)
        rows, masks, distances = summarise_access(name, net, net.graph, origins, dests)
        access_rows.extend(rows)
        masks_by_name[name] = masks
        graph_rows.append(dict(scenario=name, feature_count=len(features), physical_nodes=len(net.nodes),
                               edges=net.graph.nnz, physical_length_m=sum(f.geometry.length for f in features)))
        if name == 'source_union':
            continue
        if name == 'auto_base':
            school_mask = dests.kind.isin(SCHOOLS)
            unrestricted = net.approach_distances(school_mask)[:len(pop)]
            eligible = np.flatnonzero(~net.detached[:len(pop)])
            euclid = cKDTree(dests.loc[school_mask, ['x', 'y']]).query(origins[['x', 'y']])[0]
            fixed_group = np.zeros(len(pop), bool)
            fixed_group[eligible] = bottom_share_mask(unrestricted[eligible], pop[eligible], .2,
                tiebreak_desc=euclid[eligible], stable_id=stable_origin_rank(origins[['x', 'y']])[eligible])
            links, audit = map_gap_links(net, auto, reject_existing=True)
            candidate_ids = set(links)
            lengths = {pid: link[2] for pid, link in links.items()}
            candidate_info = audit.set_index('pid').to_dict('index')
            audit.to_csv(output/'candidate_universe.csv', index=False, encoding='utf-8-sig')
        else:
            links, audit = map_gap_links(net, auto, eligible_ids=candidate_ids)
        # Keep length budget identical across network scenarios; attachment rounding
        # cannot make a candidate cheaper in just one comparison scenario.
        links = {pid: (a, b, max(cost, lengths[pid])) for pid, (a, b, cost) in links.items()}
        if set(links) != candidate_ids:
            raise ValueError(f'{name}: candidate endpoint mapping changed; cannot compare unequal universes')
        candidate_audits.append(audit.assign(scenario=name))
        rank, bundles, selected = rank_candidates(name, net, dests, pop, fixed_group, links, lengths, candidate_info)
        ranking_rows.extend(rank); bundle_rows.extend(bundles); selection_rows.extend(selected)
        print(f'{name}: {len(links)} frozen hypothetical candidates scored', flush=True)
        if name == 'auto_unknown_restored':
            graph = net.with_links(list(links.values()))
            virtual_name = 'hypothetical_gap_sensitivity'
            rows, masks, _ = summarise_access(virtual_name, net, graph, origins, dests)
            access_rows.extend(rows); masks_by_name[virtual_name] = masks
        del net
    access = pd.DataFrame(access_rows)
    access.to_csv(output/'access_scenarios.csv', index=False, encoding='utf-8-sig')
    access[access.level.eq('city')].to_csv(output/'access_city.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame(graph_rows).to_csv(output/'network_sizes.csv', index=False)
    transitions = []
    for older, newer in (('source_union', 'auto_base'), ('auto_base', 'auto_expanded'),
                         ('auto_expanded', 'auto_unknown_restored'), ('auto_unknown_restored', 'hypothetical_gap_sensitivity')):
        for key, before in masks_by_name[older].items():
            tr = transition_summary(before, masks_by_name[newer][key], pop)
            transitions.append(dict(older=older, newer=newer, kind=key[0], cutoff_m=key[1], **tr))
            if older != 'source_union' and tr['lost_pop'] > 1e-6:
                raise AssertionError(f'nested automatic scenarios lost population: {older}->{newer}: {tr}')
    pd.DataFrame(transitions).to_csv(output/'access_transitions.csv', index=False)
    pd.DataFrame(ranking_rows).to_csv(output/'priority_individual.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame(bundle_rows).to_csv(output/'priority_bundles.csv', index=False)
    selected = pd.DataFrame(selection_rows, columns=['scenario', 'cutoff_m', 'length_budget_m', 'rule', 'pid'])
    selected.to_csv(output/'priority_selections.csv', index=False)
    pd.concat(candidate_audits, ignore_index=True).to_csv(output/'candidate_mapping.csv', index=False, encoding='utf-8-sig')
    stability = pd.DataFrame([{'pid': pid, **candidate_info[pid]} for pid in sorted(candidate_ids)],
                             columns=['pid', *next(iter(candidate_info.values())).keys()])
    for rule in ('short', 'reach', 'bottom20'):
        counts = selected[selected.rule.eq(rule)].groupby('pid').size()
        stability['selection_frequency_'+rule] = stability.pid.map(counts).fillna(0)/(len(AUTO_NAMES)*len(CUTOFFS)*len(BUDGETS))
    stability.to_csv(output/'priority_stability.csv', index=False, encoding='utf-8-sig')
    files = ['analysis/auto_scenarios.py', 'analysis/routing.py', 'analysis/priority.py',
             'analysis/candidates.py', 'analysis/osm_filter.py', 'scripts/10_auto_scenarios.py', 'requirements-auto.txt']
    manifest = {
        'method': 'auto_vector_scenarios_v1', 'human_validation': False, 'imagery_inference': False,
        'prepared_population_reused': True, 'raw_population_reproduced': False,
        'original_sample_rows': 176, 'unique_targets': len(auto), 'origin_count': len(origins),
        'destination_count': len(dests), 'population_sum': float(pop.sum()),
        'fixed_anchors_not_population': len(anchors), 'candidate_count': len(candidate_ids),
        'candidate_scope': 'sampled_51_gap_geometries_not_citywide_383_candidates',
        'budget_definition': '300m_or_600m_total_length_not_construction_cost',
        'fixed_bottom_group_population': float(pop[fixed_group].sum()),
        'fixed_bottom_group': 'worst20_percent_of_auto_base_attached_population_whole_cells',
        'scenario_names': list(masks_by_name), 'priority_combinations': len(bundle_rows),
        'routing': {'snap_m': 1., 'access_max_m': 150., 'junction_policy': 'same_level',
                    'directions_and_levels_preserved': True, 'hypothetical_links_bidirectional': True,
                    'hypothetical_links_no_midchord_junctions_or_lateral_access': True},
        'important_limits': ['automatic labels are not ground truth', 'unsampled source network remains unvalidated',
            'recorded OSM road additions use complete source ways, not just displayed sample extents',
            'hypothetical links are interventions, not confirmed current roads',
            'candidate selection frequencies are not probabilities', 'no report/presentation generation'],
        'input_files': provenance, 'automatic_csv_sha256': sha(auto_csv), 'raw_zip_sha256': RAW_SHA,
        'code_base_commit': subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
        'code_sha256': {name: sha(root/name) for name in files},
        'dependencies': {p: version(p) for p in ('numpy', 'pandas', 'scipy', 'shapely', 'pyproj')},
        'python_version': platform.python_version(),
        'elapsed_seconds': round(time.monotonic()-started, 2),
    }
    (output/'run_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: manifest[k] for k in ('candidate_count', 'priority_combinations', 'population_sum', 'elapsed_seconds')}, ensure_ascii=False), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--raw-zip', type=Path, required=True)
    p.add_argument('--judgments', type=Path)
    p.add_argument('--work-dir', type=Path)
    p.add_argument('--output-dir', type=Path)
    a = p.parse_args()
    root = a.repo_root.resolve()
    run(root, a.raw_zip, a.judgments or root/'outputs/private/auto_vector/자동판정_176.csv',
        a.work_dir or root/'outputs/runs/auto_vector_20260921', a.output_dir or root/'outputs/auto_vector')


if __name__ == '__main__':
    main()
