"""2인 독립 판독 시트 생성과 채점 (Task 6).

사용법:
    python scripts/08_judging.py --repo-root PATH sheets
    python scripts/08_judging.py --repo-root PATH score \
        [--sheet-a PATH] [--sheet-b PATH]

sheets:
- 입력: outputs/private/sample_points.csv (Task 5b 산출물. pid, sample_type,
  district, vworld_url 컬럼만 쓴다 -- stratum, source_ref, x, y, lon, lat은
  판독자에게 넘기지 않는다. 판독 기준·규칙은 validation/judging_protocol.md,
  라벨 밖 구조는 validation/judging_sheet_template.csv 참고)
- 출력: outputs/private/judging_sheet_A.csv, judging_sheet_B.csv
  (git-ignored). 판독자별 고정 시드(A=1, B=2)로 행 순서를 섞는다 -- 두 사람이
  같은 순서로 훑으며 서로 답을 맞추게 되는 상황을 피하기 위함(독립판독 원칙,
  judging_protocol.md 2절).

score:
- 입력: 두 사람이 label/confidence/imagery_date/note를 채운 시트 2개.
- label을 sample_type별 닫힌 라벨셋(LABEL_SETS)과 대조해 검증하고, 빈 라벨이
  하나라도 있으면 채점을 거부한다(개수를 나열한다).
- Cohen's kappa를 전체 + sample_type별로 계산해 outputs/judging_agreement.csv
  (sample_type별 n/kappa/n_disagree 집계 + overall 행)에 쓰고,
  judging_kappa_overall / judging_kappa_{sample_type} / judging_n_disagree를
  outputs/numbers.csv에 기록한다.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from analysis import config
from analysis.numbers import record
from analysis.sampling import cohen_kappa
from analysis.judgments import validate_matching_ids, compile_files
from analysis.judging_packet import create_manifest, read_manifest, validate_completed_sheet

# sample_type별 닫힌 라벨셋. candidate/island_gap은 "단절/연결" 판독 기준이
# 같아 세트를 공유한다(judging_protocol.md 3절).
_CONNECTIVITY_LABELS = [
    "물리적_단절", "실제로는_연결됨(기록만_끊김)", "공사중_또는_미개통", "판독불가",
]

LABEL_SETS = {
    "segment": [
        "분리형_자전거도로", "겸용_보도_표시있음", "노면표시_차로", "시설없음", "판독불가",
    ],
    "candidate": list(_CONNECTIVITY_LABELS),
    "island_gap": list(_CONNECTIVITY_LABELS),
    "placeholder": ["실재", "미실재", "판독불가"],
}

TEMPLATE_COLUMNS = [
    "pid", "sample_type", "district", "judge_id", "label",
    "confidence", "imagery_date", "note",
]
SHEET_COLUMNS = [
    "pid", "sample_type", "district", "vworld_url", "judge_id", "label",
    "confidence", "imagery_date", "note",
]

# 판독자별 셔플 시드 -- 고정값(재현 가능), 서로 다른 순서로 보게 한다.
JUDGE_SEEDS = {"A": 1, "B": 2}


def build_template(private_df: pd.DataFrame) -> pd.DataFrame:
    """비공개 표본 DataFrame에서 pid/sample_type/district만 남기고, 판독자가
    채울 빈 컬럼(judge_id, label, confidence, imagery_date, note)을 붙인다.
    좌표·URL·stratum·source_ref는 절대 포함하지 않는다(committed 파일용)."""
    template = private_df[["pid", "sample_type", "district"]].copy().reset_index(drop=True)
    for col in ("judge_id", "label", "confidence", "imagery_date", "note"):
        template[col] = ""
    return template[TEMPLATE_COLUMNS]


def write_template(private_df: pd.DataFrame, out_path: Path) -> pd.DataFrame:
    template = build_template(private_df)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    template.to_csv(out_path, index=False, encoding="utf-8-sig")
    return template


def build_judge_sheet(private_df: pd.DataFrame, judge_id: str, seed: int) -> pd.DataFrame:
    """판독자 한 명분 시트: 템플릿 컬럼 + vworld_url, 고정 시드로 행을 섞고
    judge_id를 채운다."""
    template = build_template(private_df)
    merged = template.merge(private_df[["pid", "vworld_url"]], on="pid", how="left")
    merged = merged.sample(frac=1, random_state=seed).reset_index(drop=True)
    merged["judge_id"] = judge_id
    for col in ("imagery_source", "passability"):
        merged[col] = ""
    if "target_packet" in private_df:
        merged = merged.merge(private_df[["pid", "target_packet"]], on="pid", validate="one_to_one")
    return merged[SHEET_COLUMNS + ["imagery_source", "passability"] +
                  (["target_packet"] if "target_packet" in merged else [])]


def write_judge_sheets(repo_root: Path) -> dict[str, Path]:
    private_path = repo_root / "outputs" / "private" / "sample_points.csv"
    if not private_path.exists():
        raise FileNotFoundError(f"missing Task 5b output: {private_path}")
    private_df = pd.read_csv(private_path, encoding="utf-8-sig")

    out_dir = repo_root / "outputs" / "private"
    out_dir.mkdir(parents=True, exist_ok=True)

    private_df = create_manifest(private_df, out_dir)
    paths = {}
    for judge_id, seed in JUDGE_SEEDS.items():
        sheet = build_judge_sheet(private_df, judge_id, seed)
        out_path = out_dir / f"judging_sheet_{judge_id}.csv"
        if out_path.exists():
            existing = pd.read_csv(out_path, keep_default_na=False)
            validate_matching_ids(existing, private_df, name="existing sheet (never overwritten)")
        else:
            sheet.to_csv(out_path, index=False, encoding="utf-8-sig")
        paths[judge_id] = out_path
    return paths


def validate_labels(df: pd.DataFrame) -> list[dict]:
    """label이 채워진 행만 검사해, sample_type별 닫힌 라벨셋 밖의 라벨을
    나열한다(빈 라벨은 check_empty_labels가 따로 다룬다). 알 수 없는
    sample_type도 위반으로 취급한다."""
    invalid = []
    for _, row in df.iterrows():
        label = row.get("label", "")
        if pd.isna(label) or str(label) == "":
            continue
        sample_type = row["sample_type"]
        allowed = LABEL_SETS.get(sample_type)
        if allowed is None or str(label) not in allowed:
            invalid.append({
                "pid": row.get("pid"), "sample_type": sample_type, "label": label,
            })
    return invalid


def check_empty_labels(df: pd.DataFrame) -> dict[str, int]:
    """label이 빈 행의 개수를 sample_type별로 센다(빈 sample_type은 결과에
    나타나지 않는다)."""
    labels = df["label"].fillna("").astype(str).str.strip()
    empty = df[labels == ""]
    if empty.empty:
        return {}
    return empty.groupby("sample_type").size().to_dict()


def compute_kappa(df_a: pd.DataFrame, df_b: pd.DataFrame) -> dict[str, float]:
    """pid로 두 시트를 맞춘 뒤 Cohen's kappa를 전체 + sample_type별로 계산."""
    validate_matching_ids(df_a, df_b)
    aligned = df_a[["pid", "sample_type"]].merge(df_b[["pid", "sample_type"]], on="pid")
    if not aligned.sample_type_x.eq(aligned.sample_type_y).all():
        raise ValueError("sample_type mismatch between independent sheets")
    merged = df_a[["pid", "sample_type", "label"]].merge(
        df_b[["pid", "label"]], on="pid", suffixes=("_a", "_b"),
    )
    result = {"overall": cohen_kappa(list(merged["label_a"]), list(merged["label_b"]))}
    for sample_type, g in merged.groupby("sample_type"):
        result[sample_type] = cohen_kappa(list(g["label_a"]), list(g["label_b"]))
    return result


def score(repo_root: Path, sheet_a_path: Path, sheet_b_path: Path) -> pd.DataFrame:
    df_a = pd.read_csv(sheet_a_path, encoding="utf-8-sig").fillna("")
    df_b = pd.read_csv(sheet_b_path, encoding="utf-8-sig").fillna("")

    empty_a = check_empty_labels(df_a)
    empty_b = check_empty_labels(df_b)
    if empty_a or empty_b:
        raise ValueError(
            "채점 거부: 빈 라벨이 있습니다. "
            f"sheet A 빈 라벨: {empty_a} (합계 {sum(empty_a.values())}), "
            f"sheet B 빈 라벨: {empty_b} (합계 {sum(empty_b.values())})"
        )

    invalid_a = validate_labels(df_a)
    invalid_b = validate_labels(df_b)
    if invalid_a or invalid_b:
        raise ValueError(
            "채점 거부: 닫힌 라벨셋 밖의 라벨이 있습니다. "
            f"sheet A: {invalid_a}, sheet B: {invalid_b}"
        )

    manifest = read_manifest(repo_root / "outputs/private")
    validate_completed_sheet(df_a, manifest, "A")
    validate_completed_sheet(df_b, manifest, "B")
    kappas = compute_kappa(df_a, df_b)

    merged = df_a[["pid", "sample_type", "label"]].merge(
        df_b[["pid", "label"]], on="pid", suffixes=("_a", "_b"),
    )
    merged["agree"] = merged["label_a"] == merged["label_b"]

    rows = []
    for sample_type, g in sorted(merged.groupby("sample_type"), key=lambda t: t[0]):
        rows.append({
            "sample_type": sample_type,
            "n": len(g),
            "kappa": kappas[sample_type],
            "n_disagree": int((~g["agree"]).sum()),
            "raw_agreement": float(g["agree"].mean()),
            "unknown_fraction": float(((g["label_a"] == "판독불가") | (g["label_b"] == "판독불가")).mean()),
        })
    n_disagree_total = int((~merged["agree"]).sum())
    rows.append({
        "sample_type": "overall",
        "n": len(merged),
        "kappa": kappas["overall"],
        "n_disagree": n_disagree_total,
        "raw_agreement": float(merged["agree"].mean()),
        "unknown_fraction": float(((merged["label_a"] == "판독불가") | (merged["label_b"] == "판독불가")).mean()),
    })
    agreement_df = pd.DataFrame(rows)
    agreement_df["interpretation"] = "sample-only; overall pooled-label kappa is descriptive, not a citywide accuracy certificate"

    out_path = repo_root / "outputs" / "judging_agreement.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    agreement_df.to_csv(out_path, index=False, encoding="utf-8-sig")
    merged.groupby(["sample_type", "label_a", "label_b"]).size().rename("n").reset_index().to_csv(
        repo_root / "outputs/judging_confusion.csv", index=False)

    numbers_path = repo_root / "outputs" / "numbers.csv"
    record("judging_kappa_overall", kappas["overall"],
           "2인 판독 Cohen κ (전체)", path=numbers_path)
    for sample_type in LABEL_SETS:
        if sample_type in kappas:
            record(f"judging_kappa_{sample_type}", kappas[sample_type],
                   f"2인 판독 Cohen κ ({sample_type})", path=numbers_path)
    record("judging_n_disagree", n_disagree_total,
           "2인 판독 불일치 표본 수(전체)", path=numbers_path)

    return agreement_df


def main() -> None:
    parser = argparse.ArgumentParser(
        description="2인 독립 판독 시트 생성(sheets) / 채점(score) (Task 6)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo-root", type=Path, default=config.ROOT,
                         help="입력/출력 기준 저장소 루트 (기본값: 이 저장소)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sheets", help="outputs/private/judging_sheet_{A,B}.csv 생성")

    score_parser = sub.add_parser("score", help="채워진 시트 2개를 채점")
    score_parser.add_argument("--sheet-a", type=Path, default=None)
    score_parser.add_argument("--sheet-b", type=Path, default=None)

    apply_parser = sub.add_parser("apply", help="사람이 확정한 판독을 비공개 망 수정 입력으로 변환")
    apply_parser.add_argument("--final", type=Path, required=True)
    apply_parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()

    if args.command == "apply":
        output = args.output or (repo_root / "outputs/private/corrections.json")
        if output.exists():
            raise FileExistsError("Existing corrections preserved; choose a new --output version")
        compile_files(repo_root, args.final, output)
        print(f"wrote {output}; partial sample only, not citywide validation")
    elif args.command == "sheets":
        paths = write_judge_sheets(repo_root)
        for judge_id in sorted(paths):
            print(f"wrote {paths[judge_id]}")
    elif args.command == "score":
        sheet_a = args.sheet_a or (repo_root / "outputs" / "private" / "judging_sheet_A.csv")
        sheet_b = args.sheet_b or (repo_root / "outputs" / "private" / "judging_sheet_B.csv")
        agreement_df = score(repo_root, sheet_a, sheet_b)
        print(agreement_df.to_string(index=False))
        print(f"wrote {repo_root / 'outputs' / 'judging_agreement.csv'}")


if __name__ == "__main__":
    main()
