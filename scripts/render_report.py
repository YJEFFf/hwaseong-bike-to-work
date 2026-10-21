"""보고서 원고의 숫자 토큰을 outputs/numbers.csv 값으로 치환한다.

사용법:
    python scripts/render_report.py --repo-root <root>
        [--in docs/report/report_v0.md] [--out outputs/report_v0_rendered.md]

원고(docs/report/*.md)에는 숫자를 직접 쓰지 않고 토큰으로만 쓴다.

    {{key}}        numbers.csv 의 value 문자열 그대로
    {{key|pct1}}   ×100, 소수 1자리, 뒤에 '%'           (0.2278 -> 22.8%)
    {{key|int}}    정수로 반올림, 천 단위 쉼표          (426267.8 -> 426,268)
    {{key|f2}}     소수 2자리                           (0.8785 -> 0.88)

반올림은 numbers.csv 의 문자열을 Decimal 로 읽어 ROUND_HALF_UP 으로 한다
(이진 부동소수 오차로 0.4545 -> 45.4% 가 되는 일을 막기 위함).

없는 key, 모르는 형식, 숫자가 아닌 값에 숫자 형식, 형식이 깨진 토큰이 하나라도
있으면 전부 모아 stderr 에 적고 0이 아닌 코드로 끝나며 출력 파일은 쓰지 않는다.
--in/--out 의 상대 경로는 --repo-root 기준이다.
"""
import argparse
import re
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.numbers import read_numbers  # noqa: E402

DEFAULT_IN = "docs/report/report_v1.md"
DEFAULT_OUT = "outputs/report_v1_rendered.md"

_ANY_TOKEN = re.compile(r"\{\{(.*?)\}\}")
_TOKEN_BODY = re.compile(r"\s*([A-Za-z0-9_]+)\s*(?:\|\s*([A-Za-z0-9_]+)\s*)?")


class RenderError(Exception):
    """치환 실패. 메시지에 문제가 된 토큰을 모두 담는다."""


def _to_decimal(value: str) -> Decimal:
    try:
        d = Decimal(value.strip())
    except (InvalidOperation, AttributeError):
        raise ValueError(f"not a number: {value!r}")
    if not d.is_finite():
        raise ValueError(f"not a finite number: {value!r}")
    return d


def _pct1(value: str) -> str:
    d = (_to_decimal(value) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{d}%"


def _int(value: str) -> str:
    d = _to_decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{int(d):,}"


def _f2(value: str) -> str:
    d = _to_decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{d}"


FORMATS = {"pct1": _pct1, "int": _int, "f2": _f2}


def find_tokens(text: str) -> list[tuple[str, str | None]]:
    """형식이 올바른 토큰의 (key, fmt) 목록(등장 순서, 중복 포함)."""
    out = []
    for m in _ANY_TOKEN.finditer(text):
        body = _TOKEN_BODY.fullmatch(m.group(1))
        if body:
            out.append((body.group(1), body.group(2)))
    return out


def render(text: str, numbers: dict[str, str]) -> str:
    errors: list[str] = []

    def _sub(m: re.Match) -> str:
        raw = m.group(0)
        body = _TOKEN_BODY.fullmatch(m.group(1))
        if body is None:
            errors.append(f"malformed token: {raw!r}")
            return raw
        key, fmt = body.group(1), body.group(2)
        if key not in numbers:
            errors.append(f"missing key: {key} (in {raw})")
            return raw
        if fmt is None:
            return numbers[key]
        if fmt not in FORMATS:
            errors.append(f"unknown format: {fmt} (in {raw}; known: {', '.join(FORMATS)})")
            return raw
        try:
            return FORMATS[fmt](numbers[key])
        except ValueError as e:
            errors.append(f"bad value for {raw}: {e}")
            return raw

    result = _ANY_TOKEN.sub(_sub, text)
    if errors:
        raise RenderError("\n".join(errors))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--in", dest="src", default=DEFAULT_IN)
    parser.add_argument("--out", dest="dst", default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    stderr = sys.stderr
    if hasattr(stderr, "reconfigure"):
        try:
            stderr.reconfigure(errors="backslashreplace")
        except (ValueError, OSError):
            pass

    root = Path(args.repo_root)
    src = root / args.src
    dst = root / args.dst
    numbers_path = root / "outputs" / "numbers.csv"

    try:
        numbers = read_numbers(path=numbers_path)
    except FileNotFoundError:
        print(f"numbers file not found: {numbers_path}", file=stderr)
        return 2

    text = src.read_text(encoding="utf-8")
    try:
        rendered = render(text, numbers)
    except RenderError as e:
        print(f"render failed ({src}):\n{e}", file=stderr)
        return 1

    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", encoding="utf-8", newline="") as f:
        f.write(rendered)
    tokens = find_tokens(text)
    print(f"rendered {len(tokens)} tokens ({len(set(k for k, _ in tokens))} distinct keys) -> {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
