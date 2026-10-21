"""거리/단절/인구가중을 바꾸는 회귀 반례. 공장·브이월드 실자료를 쓰지 않는다."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import LineString

from analysis.routing import RouteFeature, build_access_network
from commute.audit_support import (factory_access_network, gap_record, map_gap_records,
                                   shared_path, weighted_distance_stats, locality_key)


def test_homonymous_ri_in_different_towns_do_not_share_centroids():
    assert locality_key('경기도 화성시 봉담읍 덕우리 10') != locality_key('경기도 화성시 팔탄면 덕우리 10')
    assert locality_key('경기도 화성시 반월동 산16') == '반월동'
    assert locality_key('경기도 화성시 봉담읍 덕우리 10') == '봉담읍 덕우리'


def test_real_factory_access_cost_crosses_15_minute_boundary():
    f = [RouteFeature('road', LineString([(0, 0), (3500, 0)]))]
    n = factory_access_network(f, [[0, 0]], [[3500, 290]], 0)
    assert n.approach_distances()[0] == pytest.approx(3790.)
    assert n.approach_distances()[0] > 3750
    # 구 구현의 140m 대리점은 실제 150m를 지워 경계 안으로 잘못 넣는다.
    old = build_access_network(f, [[0, 0]], [[3500, 140]], access_max=150.)
    assert old.approach_distances()[0] == 3640


def test_factory_expansion_does_not_expand_home_or_other_destinations():
    f = [RouteFeature('road', LineString([(0, 0), (1000, 0)]))]
    n = factory_access_network(f, [[0, 200], [0, 100]], [[1000, 200], [1000, 290]], 1)
    assert n.detached.tolist() == [True, False]
    assert np.isinf(n.approach_distances([True, False])).all()
    assert n.approach_distances([False, True])[1] == pytest.approx(1390.)


def test_both_eligible_lines_remain_available_without_moving_factory():
    f = [RouteFeature('south', LineString([(0, 0), (1000, 0)])),
         RouteFeature('north', LineString([(0, 500), (1000, 500)]))]
    n = factory_access_network(f, [[0, 500]], [[1000, 240]], 0)
    assert n.approach_distances()[0] == pytest.approx(1260.)
    # 가장 가까운 남쪽 선으로 점을 옮기면 북쪽에서의 적격 접속을 잃는다.
    old = build_access_network(f, [[0, 500]], [[1000, 140]], access_max=150.)
    assert np.isinf(old.approach_distances()[0])


def test_already_attached_factory_keeps_150m_scope():
    f = [RouteFeature('south', LineString([(0, 0), (1000, 0)])),
         RouteFeature('north', LineString([(0, 350), (1000, 350)]))]
    n = factory_access_network(f, [[0, 350]], [[1000, 100]], 0)
    assert np.isinf(n.approach_distances()[0])


@pytest.mark.parametrize('tags,expected', [
    ({'highway':'path', 'foot':'designated', 'segregated':'yes'}, False),
    ({'highway':'path', 'foot':'no'}, False),
    ({'highway':'footway', 'bicycle':'designated', 'segregated':'no'}, True),
    ({'highway':'cycleway', 'foot':'designated'}, True),
    ({'highway':'cycleway'}, False),
])
def test_shared_classification_respects_explicit_tags(tags, expected):
    assert shared_path(tags) == expected


def test_exact_threshold_is_not_rounded_display_value():
    n = SimpleNamespace(nodes=np.array([[0,0],[20,1]]),levels=('0','0'))
    r = gap_record(n, 0, 1, np.hypot(20,1))
    assert r['gap_m'] == 20.
    assert r['gap_m_exact'] > 20.


def test_same_midpoint_links_have_distinct_stable_ids_and_preserved_levels():
    n = SimpleNamespace(nodes=np.array([[0,0],[2,2],[0,2],[2,0],[0,0]]),levels=('0','0','0','0','1'))
    rows = [gap_record(n,0,1,3.), gap_record(n,2,3,4.), gap_record(n,4,1,5.)]
    assert len({r['gap_id'] for r in rows}) == 3
    mapped = map_gap_records(n, rows)
    assert mapped == [(0,1,3.),(2,3,4.),(1,4,5.)]
    rows[2]['level_a'] = 'unavailable'
    with pytest.raises(ValueError, match='계층'):
        map_gap_records(n, rows)


def test_distance_statistics_weight_people_not_grid_count():
    median, mean, person_m = weighted_distance_stats([1000.,3000.,100.], [100.,1.,0.], [True,True,True])
    assert median == 1000.
    assert mean == pytest.approx(103000/101)
    assert person_m == 103000


def test_single_edge_marginal_matches_full_directed_reroute():
    f = [RouteFeature('west', LineString([(0,0),(100,0)]),direction=1),
         RouteFeature('east', LineString([(110,0),(220,0)]),direction=1)]
    n = build_access_network(f, [[10,0],[40,0]], [[210,0]], access_max=0)
    a,b,L = n.bridge_links(20.)[0]
    cutoff=205.
    base=n.distances(limit=cutoff)
    rv=n.graph.T.tocsr(); on=n.origin_nodes
    du=dijkstra(rv,directed=True,indices=a,limit=cutoff)[on]
    dv=dijkstra(rv,directed=True,indices=b,limit=cutoff)[on]
    inferred=np.minimum(base[on],np.minimum(du+L+base[b],dv+L+base[a]))
    full=n.approach_distances(limit=cutoff,graph=n.with_links([(a,b,L)]))
    assert np.array_equal(inferred <= cutoff, full <= cutoff)
    assert np.allclose(inferred, full)


def test_destination_connection_check_uses_reverse_edges():
    f=[RouteFeature('road',LineString([(0,0),(100,0)]))]
    n=build_access_network(f,[[0,0]],[[100,0],[100,200]],access_max=150)
    assert (np.diff(n.graph.T.tocsr().indptr)[n.destination_nodes] > 0).tolist() == [True,False]
    assert (np.diff(n.graph.indptr)[n.destination_nodes] > 0).tolist() == [False,False]


def test_priority_script_writes_stable_candidates_and_weighted_carbon(tmp_path, monkeypatch):
    import csv
    import pandas as pd
    monkeypatch.setenv('FACTORY_DIR',str(tmp_path))
    p=Path(__file__).resolve().parents[1]/'scripts/04_gap_priority_commute.py'
    spec=importlib.util.spec_from_file_location('test_gp',p)
    gp=importlib.util.module_from_spec(spec);spec.loader.exec_module(gp)
    features=[RouteFeature('west',LineString([(0,0),(3000,0)])),
              RouteFeature('east',LineString([(3020,0),(7000,0)]))]
    origins=pd.DataFrame(dict(x=[0.,100.],y=[0.,0.],pop=[100.,1.],district=['동탄','동탄'],dong=['가동','가동']))
    dests=pd.DataFrame(dict(x=[7000.],y=[0.],kind=['station']))
    fxy=np.array([[3500.,0.],[3500.,290.]])
    baseline=build_access_network(features,origins[['x','y']].to_numpy(),dests[['x','y']].to_numpy())
    a,b,L=baseline.bridge_links(50.)[0]
    row=dict(**gap_record(baseline,a,b,L),district='동탄',pair='공식-공식',x=3010.,y=0.)
    with (tmp_path/'gap_points_50m.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,row);writer.writeheader();writer.writerow(row)
    monkeypatch.setattr(gp,'OUT',tmp_path)
    monkeypatch.setattr(gp.m,'prepare_population',lambda _: (origins,dests))
    monkeypatch.setattr(gp.m,'load_features',lambda *_: features)
    monkeypatch.setattr(gp.ca,'working_age_weights',lambda _: np.array([100.,1.]))
    monkeypatch.setattr(gp.ca,'load_factories',lambda: (fxy,np.array([50.,50.]),np.array([False,False]),[0,0]))
    gp.main()
    chosen=pd.read_csv(tmp_path/'gap_priority_greedy.csv')
    bundles=pd.read_csv(tmp_path/'gap_bundle_carbon.csv')
    assert chosen.gap_id.tolist() == [row['gap_id']]
    assert bundles.iloc[1].new_workers_major_15min == 101
    assert bundles.iloc[1].mean_oneway_km == pytest.approx((100*3500+3400)/101/1000,abs=.0001)


def test_factory_aggregation_preserves_employees_and_separate_ri_centroids(tmp_path, monkeypatch):
    import csv
    import openpyxl
    monkeypatch.setenv('FACTORY_DIR',str(tmp_path))
    p=Path(__file__).resolve().parents[1]/'scripts/02_commute_access.py'
    spec=importlib.util.spec_from_file_location('test_ca',p)
    ca=importlib.util.module_from_spec(spec);spec.loader.exec_module(ca)
    monkeypatch.setattr(ca,'PRIVATE',tmp_path)
    rows=[('경기도 화성시 봉담읍 덕우리 1',25,'OK',100.,100.),
          ('경기도 화성시 봉담읍 덕우리 2',30,'OK',100.,100.),
          ('경기도 화성시 팔탄면 덕우리 1',40,'OK',10000.,10000.),
          ('경기도 화성시 봉담읍 덕우리 3',10,'NOT_FOUND','','')]
    wb=openpyxl.Workbook();ws=wb.active;ws.append(['title']);ws.append(['headers'])
    for i,(address,emp,*_) in enumerate(rows,1):
        ws.append([i,'','',None,None,emp,None,address])
    wb.save(tmp_path/'화성_공장등록_202402.xlsx')
    with (tmp_path/'geocode_cache.csv').open('w',newline='') as f:
        wr=csv.writer(f);wr.writerow(['address','query','status','x','y'])
        wr.writerows((a,a,s,x,y) for a,e,s,x,y in rows)
    (tmp_path/'manual_geocode.csv').write_text('pattern,x,y\n')
    xy,emp,dan,miss,approx=ca.load_factories(flags=True)
    assert emp.tolist() == [65.,40.]
    assert xy.tolist() == [[100.,100.],[10000.,10000.]]
    assert emp.sum() == 105
    assert approx.tolist() == [True,False]
