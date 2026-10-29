"""Split the integration suite into shards for the CI matrix.

    python scripts/ci_shard.py --shard 3 --of 5

Prints the integration test files of shard 3 of 5, one path per line, relative
to the backend directory (``tests/integration/test_x.py``). The files are the
``tests/integration/test_*.py`` files, sorted.

The split is a greedy longest-processing-time assignment over the per-file
durations recorded in ``tests/integration/shard_weights.json``: the heaviest
file goes first into the currently lightest shard, so the shards finish at
about the same time. Ties are broken by path, so the output is deterministic.
A file with no entry in the weights file counts as the median of the known
weights; an entry whose file no longer exists is ignored.

The weights come from the JUnit XML reports the CI shards upload as artifacts
(see ``docs/DEVELOPMENT.md``); one local ``pytest --durations`` run is the
fallback. Only their ratios matter, not their absolute values.

    python scripts/ci_shard.py --weights-from report-1.xml [report-2.xml ...]

Sums, per integration test file, the ``time`` of every ``testcase`` the reports
mention (pytest reports one testcase per test whose time is setup + call +
teardown), rounds to whole seconds and prints the new weights JSON on stdout in
the format of ``shard_weights.json``. On stderr it prints the shard totals the
old and the new weights predict from the measured times, so the operator sees
the spread before and after. A malformed report, or a testcase whose file
cannot be determined, is an error: never a silent partial result.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# The CI matrix has five shards; eight is the ceiling the workflow allows.
MAX_SHARDS = 8
INTEGRATION_DIR = "tests/integration"
WEIGHTS_FILE = "tests/integration/shard_weights.json"


def load_weights(root: Path) -> dict[str, int]:
    """The recorded durations, keyed by the path relative to the backend root.

    An entry whose file no longer exists is ignored, and an entry that is not a
    whole number of seconds is ignored too.
    """
    path = root / WEIGHTS_FILE
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    weights: dict[str, int] = {}
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if not (root / key).is_file():
            continue
        weights[key] = value
    return weights


def fallback_weight(known: list[int]) -> int:
    """The weight of a file the weights file does not know: the median.

    The median of an even number of weights is their mean, rounded to the next
    whole second. Without any known weight every file counts as one second.
    """
    if not known:
        return 1
    return max(1, int(statistics.median(known) + 0.5))


def shard_files(root: Path, of: int) -> list[list[str]]:
    """Assign every integration test file to one of ``of`` shards.

    Each shard is a list of paths relative to ``root``, sorted by path.
    """
    shards, _ = assign_shards(integration_files(root), load_weights(root), of)
    return shards


def integration_files(root: Path) -> list[str]:
    """Every integration test file, relative to ``root``, sorted."""
    return sorted(
        path.relative_to(root).as_posix()
        for path in (root / INTEGRATION_DIR).glob("test_*.py")
        if path.is_file()
    )


def assign_shards(
    files: list[str], weights: dict[str, int], of: int
) -> tuple[list[list[str]], list[int]]:
    """The greedy assignment of ``files`` to ``of`` shards, and each shard's total.

    The heaviest file first into the currently lightest shard; ties go to the
    path that sorts first, so the output is deterministic. A file with no
    weight counts as the median of the known ones.
    """
    unknown = fallback_weight(sorted(weights.values()))
    items = sorted(((weights.get(rel, unknown), rel) for rel in files), key=lambda i: (-i[0], i[1]))

    shards: list[list[str]] = [[] for _ in range(of)]
    totals = [0] * of
    for weight, rel in items:
        # The lightest shard takes the file; a tie goes to the first shard.
        index = min(range(of), key=lambda i: (totals[i], i))
        shards[index].append(rel)
        totals[index] += weight
    return [sorted(shard) for shard in shards], totals


def file_of_classname(classname: str) -> str | None:
    """The backend-relative test file a JUnit classname points at.

    Pytest writes the dotted module path (``tests.integration.test_x``) and
    appends the test classes, so the file is the dotted path up to its last
    ``test_*`` component. ``None`` when the testcase names no file this
    repository owns.
    """
    parts = classname.split(".")
    while parts:
        if parts[-1].startswith("test_"):
            return "/".join(parts) + ".py"
        parts.pop()
    return None


def measure_seconds(reports: list[Path]) -> dict[str, float]:
    """The summed testcase seconds per test file, across the JUnit reports.

    Pytest reports one ``testcase`` per test whose ``time`` is setup + call +
    teardown (``junit_duration_report = total``, the default). A malformed
    report, or a testcase whose file or time cannot be read, is an error: the
    weights must never be a silent partial result.
    """
    seconds: dict[str, float] = {}
    for report in reports:
        try:
            # The reports come from our own CI, not from untrusted input.
            root = ET.parse(report).getroot()  # noqa: S314
        except (OSError, ET.ParseError) as error:
            raise ValueError(f"{report}: cannot read the JUnit XML report ({error})") from None
        for case in root.iter("testcase"):
            classname = case.get("classname", "")
            rel = file_of_classname(classname)
            name = case.get("name", "")
            if rel is None:
                raise ValueError(f"{report}: no test file for testcase {classname}::{name}")
            try:
                seconds[rel] = seconds.get(rel, 0.0) + float(case.get("time", ""))
            except ValueError:
                raise ValueError(f"{report}: no time for testcase {classname}::{name}") from None
    return seconds


def weights_from(seconds: dict[str, float], root: Path) -> dict[str, int]:
    """The new weights: whole seconds, at least one, in the weights-file format.

    A file the reports mention that is not on disk is skipped, and a file on
    disk the reports do not mention is left out (it then counts as the median,
    as today); both are named on stderr.
    """
    weights: dict[str, int] = {}
    for rel in sorted(seconds):
        if not (root / rel).is_file():
            print(f"skipped, not on disk: {rel}", file=sys.stderr)
            continue
        # Half a second rounds up; a 0 weight would put a file nowhere in
        # particular, so the smallest honest weight is 1.
        weights[rel] = max(1, int(seconds[rel] + 0.5))
    for rel in integration_files(root):
        if rel not in seconds:
            print(f"no testcase in the reports, left out: {rel}", file=sys.stderr)
    return weights


def _median_time(seconds: dict[str, float]) -> float:
    """The median of the measured times, for files with no measured time."""
    if not seconds:
        return 0.0
    return statistics.median(sorted(seconds.values()))


def print_table(root: Path, weights: dict[str, int], seconds: dict[str, float], of: int) -> None:
    """The shard totals the old and the new weights predict, on stderr.

    For each of ``old`` (from ``load_weights``) and ``new`` (from ``weights``):
    take the shard assignment that weight file produces (the file lists from
    ``assign_shards``), and sum, per shard, the measured seconds of its
    files. A file with no measured time counts as the median of the measured
    times. Whole seconds are printed with half-up rounding (like the weights),
    and the spread is ``max / min``.
    """
    files = integration_files(root)
    old_assign, _ = assign_shards(files, load_weights(root), of)
    new_assign, _ = assign_shards(files, weights, of)

    def totals_for(assignments: list[list[str]]) -> list[int]:
        median_time = _median_time(seconds)
        result: list[int] = []
        for shard_files in assignments:
            shard_total = 0.0
            for rel in shard_files:
                shard_total += seconds.get(rel, median_time)
            result.append(int(shard_total + 0.5))
        return result

    old_totals = totals_for(old_assign)
    new_totals = totals_for(new_assign)
    print(f"predicted shard seconds from the measured times ({of} shards):", file=sys.stderr)
    print(f"{'shard':>5} {'old':>5} {'new':>5}", file=sys.stderr)
    for index, (before, after) in enumerate(zip(old_totals, new_totals, strict=True), 1):
        print(f"{index:>5d} {before:>5d} {after:>5d}", file=sys.stderr)
    print(f"{'spread':>5} {spread(old_totals):>5} {spread(new_totals):>5}", file=sys.stderr)


def spread(totals: list[int]) -> str:
    """max/min of the shard totals, or ``-`` when a shard is empty."""
    smallest = min(totals)
    return "-" if smallest == 0 else f"{max(totals) / smallest:.2f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print one shard's files, or new shard weights from JUnit XML reports."
    )
    parser.add_argument("--shard", type=int, help="the shard to print, 1-based")
    parser.add_argument("--of", type=int, help="the number of shards (default: 5)")
    parser.add_argument(
        "--weights-from",
        nargs="+",
        action="append",
        type=Path,
        metavar="FILE.XML",
        help="the JUnit XML reports to weigh the integration test files from",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="the backend directory that holds tests/ (default: this script's own)",
    )
    args = parser.parse_args(argv)

    of = 5 if args.of is None else args.of
    if not 1 <= of <= MAX_SHARDS:
        print(f"--of must be between 1 and {MAX_SHARDS}", file=sys.stderr)
        return 2

    if args.weights_from is not None:
        if args.shard is not None:
            print("--shard and --weights-from cannot be combined", file=sys.stderr)
            return 2
        reports = [report for group in args.weights_from for report in group]
        try:
            seconds = measure_seconds(reports)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 2
        weights = weights_from(seconds, args.root)
        print(json.dumps(weights, sort_keys=True, indent=2))
        print_table(args.root, weights, seconds, of)
        return 0

    if args.shard is None:
        print("--shard or --weights-from is required", file=sys.stderr)
        return 2
    if not 1 <= args.shard <= of:
        print(f"--shard must be between 1 and --of ({of})", file=sys.stderr)
        return 2

    for rel in shard_files(args.root, of)[args.shard - 1]:
        print(rel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
