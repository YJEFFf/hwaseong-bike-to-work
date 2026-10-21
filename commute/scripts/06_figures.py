"""재설계 보고서 그림 1·2 (그림 3은 12_보고서초안_0925/보강분석/figures/fig3_dongtan8_crossing.png 재사용).
그림 1: 두 지도 합집합 자전거망의 연결 덩어리와 끊긴 지점(끝점 틈 20m 이내), 출퇴근 우선순위 상위 10곳.
그림 2: 구별 '직선 15분 거리 안 공장 일자리 중 자전거망으로 닿는 몫'(접속 150m·300m, 동네길 포함 상한).
공장 좌표는 그리지 않는다(브이월드 약관). 한글 문서에 폭 125mm로 넣을 때 글자 약 8pt 이상. 실행: 제출레포 루트에서."""
import csv, importlib.util, json, math, os
from pathlib import Path
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
import numpy as np
from matplotlib.collections import LineCollection
from matplotlib import font_manager
from pyproj import Transformer
from scipy.sparse.csgraph import connected_components
from shapely.geometry import shape
from shapely.ops import transform as _tf

for font in filter(None, (os.environ.get('COMMUTE_FONT'), 'Malgun Gothic', 'Noto Sans CJK KR', 'NanumGothic', 'AppleGothic', 'Arial Unicode MS')):
    try:
        font_manager.findfont(font, fallback_to_default=False)
        plt.rcParams['font.family'] = font
        break
    except ValueError:
        continue
else:
    raise RuntimeError('한글 글꼴을 찾지 못했습니다. COMMUTE_FONT에 설치된 한글 글꼴 이름을 지정하세요.')
plt.rcParams['axes.unicode_minus'] = False
HERE = Path(__file__).resolve().parent; RES = Path(os.environ.get('COMMUTE_OUT', HERE.parent/'results')); FIG = Path(os.environ.get('COMMUTE_FIG', HERE.parent/'figures')); FIG.mkdir(parents=True, exist_ok=True)
ROOT = Path.cwd(); WORK = ROOT/'outputs/runs/loo_work'
spec = importlib.util.spec_from_file_location('auto10', ROOT/'scripts/10_auto_scenarios.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

# ---------- 그림 1 ----------
origins, dests = m.prepare_population(WORK)
net = m.build_access_network(m.load_features(WORK, 'union'), origins[['x', 'y']].to_numpy(float), dests[['x', 'y']].to_numpy(float), snap=1., access_max=150.)
weak = net.physical_graph.maximum(net.physical_graph.T)
_, comp = connected_components(weak, directed=False)
coo = weak.tocoo(); keep = coo.row < coo.col
u, v, w = coo.row[keep], coo.col[keep], coo.data[keep]
clen = np.bincount(comp[u], weights=w)
order = np.argsort(-clen)
big = comp[u] == order[0]
small = clen[comp[u]] < 1000
to5186 = Transformer.from_crs(4326, 5186, always_xy=True).transform
fc = json.loads((WORK/'data/raw/admin/hwaseong_hjd_20260701.geojson').read_text(encoding='utf-8'))
districts = {}
for f in fc['features']:
    for k in ('동탄', '병점', '효행', '만세'):
        if k+'구' in f['properties'].get('sggnm', ''):
            g = _tf(to5186, shape(f['geometry']))
            districts[k] = g if k not in districts else districts[k].union(g)
gaps = list(csv.DictReader(open(RES/'gap_points_50m.csv', encoding='utf-8')))
gaps20 = np.array([(float(r['x']), float(r['y'])) for r in gaps if float(r['gap_m_exact']) <= 20])
top = list(csv.DictReader(open(RES/'gap_priority_greedy.csv', encoding='utf-8')))[:10]
tx, ty = np.array([float(r['x']) for r in top]), np.array([float(r['y']) for r in top])
seg = lambda mask: np.stack([net.nodes[u[mask]], net.nodes[v[mask]]], axis=1)


def draw(ax, lw, zoom):
    for geom in districts.values():
        for poly in getattr(geom, 'geoms', [geom]):
            ax.plot(*poly.exterior.xy, color='#BBBBBB', lw=0.6, zorder=1)
    ax.add_collection(LineCollection(seg(~big & ~small), colors='#56B4E9', lw=lw, zorder=2))
    ax.add_collection(LineCollection(seg(small), colors='#E69F00', lw=lw, zorder=3))
    ax.add_collection(LineCollection(seg(big), colors='#0B3C74', lw=lw*1.2, zorder=4))
    ax.scatter(gaps20[:, 0], gaps20[:, 1], s=6 if not zoom else 10, color='#D62728', lw=0, zorder=5)
    ax.set_aspect('equal'); ax.set_xticks([]); ax.set_yticks([])


with plt.rc_context({'font.size': 11}):
    fig, (ax, az) = plt.subplots(1, 2, figsize=(6.3, 3.4), dpi=300, gridspec_kw={'width_ratios': [1.35, 1]})
    draw(ax, 0.8, False)
    b = np.array([g.bounds for g in districts.values()])
    ax.set_xlim(b[:, 0].min()-1000, b[:, 2].max()+1000); ax.set_ylim(b[:, 1].min()-1000, b[:, 3].max()+1000)
    for k in ('효행', '만세'):
        g = districts[k]; p = max(getattr(g, 'geoms', [g]), key=lambda q: q.area).representative_point()
        ax.text(p.x, p.y, f'{k}구', fontsize=10.5, fontweight='bold', color='#333', ha='center', va='center',
                path_effects=[pe.withStroke(linewidth=3, foreground='white')], zorder=8)
    x0, x1, y0, y1 = tx.min()-1800, tx.max()+1800, ty.min()-1500, ty.max()+1500
    ax.add_patch(plt.Rectangle((x0, y0), x1-x0, y1-y0, fill=False, ec='#333', lw=0.9, zorder=9))
    ax.plot([b[:, 2].max()-6000, b[:, 2].max()-1000], [b[:, 1].min()]*2, color='k', lw=2)
    ax.text(b[:, 2].max()-3500, b[:, 1].min()+800, '5 km', ha='center', fontsize=9.5)
    for s_ in ax.spines.values():
        s_.set_visible(False)
    draw(az, 1.1, True)
    az.set_xlim(x0, x1); az.set_ylim(y0, y1)
    placed = []
    for i, (x, y) in enumerate(zip(tx, ty), 1):
        lx, ly = x, y
        if any(np.hypot(lx-px, ly-py) < 700 for px, py in placed):  # 가까운 번호끼리 겹치면 옆으로 벌리고 지시선(확대 패널에서 원 지름 약 660m)
            lx, ly = x+650, y+650
            az.plot([x, lx], [y, ly], color='#D62728', lw=0.8, zorder=5)
        placed.append((lx, ly))
        az.scatter([lx], [ly], s=95, facecolor='white', edgecolor='#D62728', lw=1.4, zorder=6)
        az.text(lx, ly, str(i), fontsize=8, fontweight='bold', color='#D62728', ha='center', va='center', zorder=7)
    for k, (dx, dy) in (('동탄', (0, -1200)), ('병점', (300, -2300))):  # 번호 원과 겹치지 않게 조금 옮김
        g = districts[k]; p = max(getattr(g, 'geoms', [g]), key=lambda q: q.area).representative_point()
        if x0 < p.x < x1 and y0 < p.y < y1:
            az.text(p.x+dx, p.y+dy, f'{k}구', fontsize=10.5, fontweight='bold', color='#333', ha='center', va='center', alpha=0.85,
                    path_effects=[pe.withStroke(linewidth=3, foreground='white')], zorder=8)
    az.set_title('동탄·병점 확대: 출퇴근 효과 순위(50m 안 끊긴 지점)', fontsize=9)
    ax.plot([], [], color='#0B3C74', lw=2, label='가장 큰 연결 덩어리')
    ax.plot([], [], color='#56B4E9', lw=2, label='다른 덩어리(1km 이상)')
    ax.plot([], [], color='#E69F00', lw=2, label=f'1km 미만 섬({(clen < 1000).sum()}개)')
    ax.scatter([], [], s=12, color='#D62728', label=f'20m 안에서 끊긴 지점({len(gaps20)}곳)')
    ax.legend(loc='lower left', fontsize=8.5, frameon=True, handlelength=1.3, borderpad=0.3, labelspacing=0.3)
    fig.tight_layout(w_pad=0.5); fig.savefig(FIG/'fig1_broken_network.png', bbox_inches='tight', pad_inches=0.04); plt.close(fig)  # 아래 여백 자르기

# ---------- 그림 2 ----------
def share(path, network):
    rows = [r for r in csv.DictReader(open(path, encoding='utf-8')) if r['network'] == network and r['cutoff_m'] == '3750.0']
    return {r['area']: float(r['bike_share_of_straight'])*100 for r in rows}
R = RES/'commute_access.csv'
sens = {r['항목']: r['값'] for r in csv.DictReader(open(RES/'sensitivity_round2.csv', encoding='utf-8'))}
ex = [float(v.strip().rstrip('%')) for v in next(v for k, v in sens.items() if ('최대 종업원 집계점 제외' in k or '삼성 한 점 제외' in k) and '/' in v).split('/')]
areas = ['화성시', '최대 사업장 제외', '동탄', '병점', '효행', '만세']
series = [('현재', share(R, 'union'), '#0B3C74'), ('끊긴 틈(20m) 모두 연결', share(R, 'union+끝점20m'), '#56B4E9'),
          ('공장 진입 구간(150~300m) 연결', share(R, 'union+공장접속300m'), '#D62728'), ('둘 다', share(R, 'union+공장접속300m+끝점20m'), '#7F7F7F')]
fig, ax = plt.subplots(figsize=(6.2, 3.0), dpi=300)
wd = 0.2
for i, (lab, s_, c) in enumerate(series):
    s_['최대 사업장 제외'] = ex[i]  # 같은 좌표로 묶은 종업원이 가장 많은 한 점 제외
    xs = [j+(i-1.5)*wd for j in range(len(areas))]
    vals = [s_[a] for a in areas]
    ax.bar(xs, vals, wd, label=lab, color=c, edgecolor='white')
    for x, val in zip(xs, vals):
        if val >= 1:
            ax.text(x, val+0.5, f'{val:.1f}', ha='center', va='bottom', fontsize=7.5)
for j, a in enumerate(areas):
    if max(s_[a] for _, s_, _ in series) < 1:
        ax.text(j, 1.2, '모두 1% 미만', ha='center', va='bottom', fontsize=9, color='#555')
ax.set_xticks(range(len(areas))); ax.set_xticklabels(['화성시 전체' if a == '화성시' else ('최대 사업장' + chr(10) + '제외' if a == '최대 사업장 제외' else f'{a}구') for a in areas], fontsize=10.5)
ax.axvline(1.5, color='#999', lw=0.8, ls=':')
top = max(max(s_[a] for a in areas) for _, s_, _ in series)
ax.set_ylabel('자전거로 닿는 몫(%)', fontsize=11); ax.set_ylim(0, max(30, 10 * math.ceil((top + 3) / 10))); ax.tick_params(axis='y', labelsize=10.5)  # 가장 큰 막대와 값 표시가 잘리지 않게
ax.spines[['top', 'right']].set_visible(False)
ax.legend(ncol=2, frameon=False, loc='upper center', bbox_to_anchor=(0.5, 1.27), fontsize=10, handlelength=1.2, columnspacing=1.0)
fig.tight_layout(); fig.savefig(FIG/'fig2_commute_jobs_share.png'); plt.close(fig)
print('ok')
