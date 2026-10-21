"""Non-destructive runner: preflight -> isolated run -> complete/failed manifest.

Historical outputs and human sheets are never overwritten. Successful runs live
in outputs/runs/<id>/ with a complete code/input snapshot (private, git-ignored).
--prepared-inputs reuses saved population/POI derivatives, NOT raw reproduction.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis import config

REGIONS = "향남읍,동탄6동"


def build_steps(repo_root, fetch=False, prepared_inputs=False, corrections=None, quick=False):
    root = Path(repo_root)
    steps = []
    def add(name, args=(), run=True):
        steps.append(dict(name=name, script=root/"scripts"/(name+".py"), args=list(args), run=run))
    rr = ["--repo-root", str(root)]
    add("00_fetch", run=fetch)
    for name in ("01_verify", "01b_osm_stats", "01c_official_topology", "02_agreement"):
        add(name)
    add("02b_origins_destinations", ["--raw", str(root/"data/raw")], run=not prepared_inputs)
    # A frozen packet belongs to its original sample universe. Never resample it.
    add("03_sample", rr, run=not (root/"outputs/private/sample_points.csv").exists())
    correction_flags = ["--corrections", str(corrections)] if corrections else []
    add("04_access", rr + (["--prepared-inputs"] if prepared_inputs else []) + correction_flags)
    add("04b_lowstress_stats", rr)
    add("04c_select_regions", rr)
    add("05_priority", rr + ["--regions", REGIONS] + correction_flags + (["--quick"] if quick else []))
    add("06_carbon", rr + ["--regions", REGIONS])
    add("07_figures", rr)
    add("render_report", rr + ["--in", "docs/report/report_v1.md", "--out", "outputs/report_v1_rendered.md"])
    return steps


def truncate_at(steps, until):
    if until is None:
        return steps
    names = [s["name"] for s in steps]
    if until not in names:
        raise SystemExit(f"unknown --until {until}; choose {names}")
    return steps[:names.index(until)+1]


def required_inputs(root, prepared=False):
    root = Path(root)
    manifest = json.loads((root/"data_manifest.json").read_text())
    paths = [row["path"] for row in manifest["raw_inputs"]]
    if prepared:
        paths = [p for p in paths if not any(
            p.startswith("data/raw/"+d+"/") for d in ("population", "ghsl", "schools", "osm_poi"))]
        paths += ["outputs/origins_5186.csv", "outputs/destinations_5186.csv"]
    return sorted(set(paths))


def preflight(root, prepared=False, fetch=False):
    paths = required_inputs(root, prepared)
    fetched = {"data/raw/화성_자전거노선_5186.geojson", "data/raw/화성_자전거도로면_5186.geojson"} if fetch else set()
    missing = [p for p in paths if p not in fetched and not (Path(root)/p).is_file()]
    if missing:
        raise FileNotFoundError("Required inputs missing; existing outputs unchanged:\n" + "\n".join(missing))
    return paths


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _git(root, *args):
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else "unavailable"


def prepare_run(root, inputs):
    root = Path(root)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8]
    target = root/"outputs/runs"/run_id
    target.mkdir(parents=True, exist_ok=False)
    for name in ("analysis", "scripts", "params", "docs", "validation"):
        shutil.copytree(root/name, target/name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(root/"data_manifest.json", target/"data_manifest.json")
    for rel in inputs:
        source = root/rel
        if source.is_file():
            dst = target/rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dst)
    # Human sheets/manifest are frozen inputs, NOT generated tables to overwrite.
    private = root/"outputs/private"
    if private.exists():
        for path in private.iterdir():
            if path.name == "sample_points.csv" or path.name.startswith(("judging_", "final_judgment", "corrections")):
                dest = target/"outputs/private"/path.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                if path.is_dir():
                    shutil.copytree(path, dest)
                else:
                    shutil.copy2(path, dest)
    return target


def run_steps(steps):
    for step in steps:
        if not step["run"]:
            print(f"-- skip {step['name']}", flush=True)
            continue
        command = [sys.executable, str(step["script"]), *step["args"]]
        print(f"-- run {step['name']}", flush=True)
        result = subprocess.run(command, cwd=step["script"].parents[1])
        if result.returncode:
            raise RuntimeError(f"{step['name']} failed: exit {result.returncode}")
        if step["name"] == "04c_select_regions":
            # Keep the team's selected cases visible even if the new routing
            # model changes the original rule's winners. Never silently switch.
            import pandas as pd
            root = step["script"].parents[1]
            table = pd.read_csv(root/"outputs/region_selection.csv")
            winners = set(table.loc[table.rank_in_district.eq(1), "dong"])
            agreed = set(REGIONS.split(","))
            if winners != agreed:
                raise RuntimeError(f"Region-selection result changed: {sorted(winners)} vs agreed {sorted(agreed)}; team decision required before policy tables")


def execute(root, *, prepared=False, fetch=False, until=None, corrections=None, quick=False):
    root = Path(root).resolve()
    # Validate options and all prerequisites BEFORE any output mutation.
    truncate_at(build_steps(root, fetch, prepared), until)
    inputs = preflight(root, prepared, fetch)
    if corrections is not None and not Path(corrections).is_file():
        raise FileNotFoundError(corrections)
    target = prepare_run(root, inputs)
    correction_copy = None
    if corrections:
        correction_copy = target/"outputs/private/applied_corrections.json"
        correction_copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(corrections, correction_copy)
    state = dict(status="running", source_head=_git(root, "rev-parse", "HEAD"),
                 source_dirty_status=_git(root, "status", "--short"), python=sys.version,
                 prepared_derivatives=prepared, quick_baseline_only=quick,
                 corrections_sha256=sha256(correction_copy) if correction_copy else None,
                 human_validation="partial_sample_only" if corrections else "unjudged")
    status_path = target/"run_manifest.json"
    def save():
        status_path.write_text(json.dumps(state, ensure_ascii=False, indent=2)+"\n")
    try:
        if fetch:
            run_steps(build_steps(target, fetch=True)[:1])
        preflight(target, prepared, False)
        state["inputs"] = {rel: sha256(target/rel) for rel in inputs}
        state["code"] = {str(p.relative_to(target)): sha256(p) for directory in ("analysis", "scripts", "params", "docs")
                         for p in (target/directory).rglob("*") if p.is_file() and "__pycache__" not in p.parts}
        save()
        if prepared:
            import pandas as pd
            from analysis.numbers import record
            pop = pd.read_csv(target/"outputs/origins_5186.csv")["pop"].sum()
            record("pop_total_hwaseong", float(pop), "saved derived origins, not raw population reproduction",
                   path=target/"outputs/numbers.csv")
        steps = truncate_at(build_steps(target, False, prepared, correction_copy, quick), until)
        state["commands"] = [[s["name"], s["args"]] for s in steps if s["run"]]
        save()
        run_steps(steps)
        state["status"] = "partial" if until else "complete_provisional"
        state["outputs"] = {str(p.relative_to(target)): sha256(p) for p in (target/"outputs").rglob("*") if p.is_file()}
        save()
        if until is None:
            # A pointer only. Do not copy over historical tables or reports.
            (root/"outputs/LATEST_RUN.txt").write_text(str(target.relative_to(root))+"\n")
        return target
    except BaseException as exc:
        state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        save()
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", type=Path, default=config.ROOT)
    p.add_argument("--fetch", action="store_true", help="fetch ONLY official WFS files, inside isolated run")
    p.add_argument("--prepared-inputs", action="store_true")
    p.add_argument("--corrections", type=Path)
    p.add_argument("--quick", action="store_true")
    p.add_argument("--until")
    p.add_argument("--check-inputs", action="store_true")
    a = p.parse_args()
    if a.check_inputs:
        preflight(a.repo_root, a.prepared_inputs, a.fetch)
        print("All required files present (contents/rights not certified).")
    else:
        print(execute(a.repo_root, prepared=a.prepared_inputs, fetch=a.fetch, until=a.until,
                      corrections=a.corrections, quick=a.quick))


if __name__ == "__main__":
    main()
