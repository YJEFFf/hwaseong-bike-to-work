"""그림 2: 자전거망 버전(OSM/공식/합집합/합집합+저위계) x 구별 학교(2.5km)
접근 인구 비율(%) 그룹 막대 그림.

outputs/numbers.csv 에서 값을 읽어 analysis.figures.plot_access_by_network에
넘기기만 한다 -- 그리는 로직은 전부 analysis/figures.py에 있다.

사용법:
    python scripts/07_figures.py --repo-root <root>
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import config
from analysis.figures import (
    ACCESS_GROUP_ORDER,
    ACCESS_VERSIONS,
    load_access_by_network,
    load_detached_share,
    plot_access_by_network,
)
from analysis.numbers import read_numbers


def run(repo_root: Path):
    repo_root = Path(repo_root)
    numbers_path = repo_root / "outputs" / "numbers.csv"
    numbers = read_numbers(path=numbers_path)

    values = load_access_by_network(numbers)
    detached_share = load_detached_share(numbers)

    out_path = repo_root / "outputs" / "figures" / "fig2_access_by_network.png"
    result = plot_access_by_network(values, detached_share, out_path)

    print(f"wrote {result}")
    for version in ACCESS_VERSIONS:
        row = ", ".join(f"{g}={values[version][g]:.1f}%" for g in ACCESS_GROUP_ORDER)
        print(f"  {version}: {row}")
    print(f"detached_share={detached_share:.1f}%")
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path, default=config.ROOT)
    args = ap.parse_args()

    run(args.repo_root)


if __name__ == "__main__":
    main()
