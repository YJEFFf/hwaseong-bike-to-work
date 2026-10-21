"""개선 대안 강건성 지표.

Step 1-4 (task-13): 여러 랭킹(예: 정의 격자 전체에서의 top-N 후보 순위) 사이의
합의를 정량화한다.

- `jaccard_topn`: 모든 쌍의 top-n Jaccard 유사도의 중앙값.
- `topn_inclusion`: 각 항목이 몇 개의 랭킹에서 top-n에 포함되는지의 비율.

Steps 5-8(연결 후보, 링크 한계편익, 순위 규칙, 강건성 그리드)은
`analysis/candidates.py`(Task 13b)에 구현되어 있다 -- 여기 두 함수는 그
그리드의 top-N 랭킹들을 비교하는 데 쓰인다 (final-review M5: 이 docstring이
"후속 태스크에서 구현한다"고 적어 이미 구현된 상태와 어긋났다).
"""

import statistics
from itertools import combinations


def jaccard_topn(rankings: list[list[int]], n: int = 20) -> float:
    """모든 랭킹 쌍의 top-n Jaccard 유사도 중앙값.

    각 랭킹에서 상위 n개를 집합으로 보고, 모든 쌍에 대해
    |교집합| / |합집합|을 구한 뒤 그 중앙값을 반환한다.
    """
    if len(rankings) < 2:
        raise ValueError("jaccard_topn requires at least 2 rankings")

    top_sets = [set(ranking[:n]) for ranking in rankings]

    similarities = []
    for a, b in combinations(top_sets, 2):
        union = a | b
        intersection = a & b
        similarities.append(len(intersection) / len(union) if union else 1.0)

    return statistics.median(similarities)


def topn_inclusion(rankings: list[list[int]], n: int = 20) -> dict[int, float]:
    """각 항목이 top-n에 포함된 랭킹의 비율.

    예: [[1, 2], [1, 3]], n=2 -> {1: 1.0, 2: 0.5, 3: 0.5}
    """
    total = len(rankings)
    counts: dict[int, int] = {}

    for ranking in rankings:
        for item in set(ranking[:n]):
            counts[item] = counts.get(item, 0) + 1

    return {item: count / total for item, count in counts.items()}
