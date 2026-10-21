"""그림 4: 직무에 운전·근무지 밖 이동이 있는 공고를 뺀 자차 조건 공고 비율 — (가) 구별, (나) 근무지에서 자전거망까지 거리별.

축은 패널마다 하나. 구별 자전거 접근 지표는 두 번째 축 대신 구 이름 아래 글자로 적는다.
"""
import csv, os
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

HERE = Path(__file__).resolve().parents[1]
RES = Path(os.environ.get('JOBS_OUT', HERE / 'results'))
FIG = Path(os.environ.get('JOBS_FIG', HERE / 'figures'))
FIG.mkdir(parents=True, exist_ok=True)
for name in (os.environ.get('COMMUTE_FONT'), 'Malgun Gothic', 'NanumGothic', 'AppleGothic'):
    if name and any(f.name == name for f in font_manager.fontManager.ttflist):
        plt.rcParams['font.family'] = name
        break
plt.rcParams['axes.unicode_minus'] = False
BAR, INK, MUTED = '#0B3C74', '#222222', '#666666'


def read(name):
    return list(csv.DictReader(open(RES / name, encoding='utf-8-sig')))


gu = [r for r in read('jobs_by_gu.csv') if r['group'] in ('만세구', '효행구', '병점구', '동탄구')]
dist = [r for r in read('jobs_by_bike_distance.csv') if r['group'] != '좌표 없음']

fig, axes = plt.subplots(1, 2, figsize=(6.4, 2.9), dpi=300, gridspec_kw=dict(width_ratios=[1.25, 1], wspace=0.22))
ymax = max(float(r['car_nonwork_share']) for r in gu + dist) * 100
top = 5 * int(ymax / 5 + 2)

ax = axes[0]
xs = range(len(gu))
vals = [float(r['car_nonwork_share']) * 100 for r in gu]
ax.bar(xs, vals, 0.6, color=BAR, edgecolor='white', linewidth=1.5)
for x, v, r in zip(xs, vals, gu):
    ax.text(x, v + top * 0.02, f'{v:.1f}', ha='center', va='bottom', fontsize=8, color=INK)
labels = [f"{r['group']}\n({int(r['n']):,}건)\n자전거 {float(r['bike_job_access_share']) * 100:.1f}%" for r in gu]
ax.set_xticks(list(xs)); ax.set_xticklabels(labels, fontsize=6.5, color=INK, linespacing=1.2)
ax.set_title('(가) 구별', fontsize=9.5, color=INK, loc='left')

ax = axes[1]
xs = range(len(dist))
vals = [float(r['car_nonwork_share']) * 100 for r in dist]
ax.bar(xs, vals, 0.6, color=BAR, edgecolor='white', linewidth=1.5)
for x, v in zip(xs, vals):
    ax.text(x, v + top * 0.02, f'{v:.1f}', ha='center', va='bottom', fontsize=8, color=INK)
ax.set_xticks(list(xs)); ax.set_xticklabels([f"{r['group']}\n({int(r['n']):,}건)" for r in dist], fontsize=6.8, color=INK, linespacing=1.2)
ax.set_title('(나) 근무지~가장 가까운 자전거 노선', fontsize=9.5, color=INK, loc='left')

for ax in axes:
    ax.set_ylim(0, top)
    ax.spines[['top', 'right']].set_visible(False)
    ax.spines[['left', 'bottom']].set_color('#999999')
    ax.tick_params(axis='y', labelsize=7.5, colors=MUTED, length=2)
    ax.tick_params(axis='x', length=0)
    ax.grid(axis='y', color='#e5e5e5', linewidth=0.6); ax.set_axisbelow(True)
axes[0].set_ylabel('자차 조건 공고 비율(%)', fontsize=8.5, color=INK)
fig.savefig(FIG / 'fig4_jobs_car_condition.png', bbox_inches='tight', pad_inches=0.04)
print('ok')
