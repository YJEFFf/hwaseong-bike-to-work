import csv

from analysis import config


def record(key: str, value, note: str = "", path=None) -> None:
    """Upsert a (key, value, note) row into a numbers CSV.

    Replacing an existing key keeps a single row for that key.
    """
    if path is None:
        path = config.OUTPUTS / "numbers.csv"

    path.parent.mkdir(parents=True, exist_ok=True)

    rows = {}
    if path.exists():
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                rows[row["key"]] = {"value": row["value"], "note": row["note"]}

    rows[key] = {"value": str(value), "note": note}

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["key", "value", "note"])
        writer.writeheader()
        for k, v in rows.items():
            writer.writerow({"key": k, "value": v["value"], "note": v["note"]})


def read_numbers(path=None) -> dict[str, str]:
    if path is None:
        path = config.OUTPUTS / "numbers.csv"

    numbers = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            numbers[row["key"]] = row["value"]
    return numbers
