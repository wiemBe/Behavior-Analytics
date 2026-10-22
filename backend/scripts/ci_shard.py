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

The weights come from one local ``pytest --durations`` run (see
``docs/DEVELOPMENT.md``). Only their ratios matter, not their absolute values.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
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
    files = sorted(p for p in (root / INTEGRATION_DIR).glob("test_*.py") if p.is_file())
    weights = load_weights(root)
    unknown = fallback_weight(sorted(weights.values()))

    items = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        items.append((weights.get(rel, unknown), rel))
    # The heaviest file first; a tie goes to the path that sorts first.
    items.sort(key=lambda item: (-item[0], item[1]))

    shards: list[list[str]] = [[] for _ in range(of)]
    totals = [0] * of
    for weight, rel in items:
        # The lightest shard takes the file; a tie goes to the first shard.
        index = min(range(of), key=lambda i: (totals[i], i))
        shards[index].append(rel)
        totals[index] += weight
    return [sorted(shard) for shard in shards]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the integration test files of one shard.")
    parser.add_argument("--shard", type=int, required=True, help="the shard to print, 1-based")
    parser.add_argument("--of", type=int, required=True, help="the number of shards")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="the backend directory that holds tests/ (default: this script's own)",
    )
    args = parser.parse_args(argv)

    if not 1 <= args.of <= MAX_SHARDS:
        print(f"--of must be between 1 and {MAX_SHARDS}", file=sys.stderr)
        return 2
    if not 1 <= args.shard <= args.of:
        print(f"--shard must be between 1 and --of ({args.of})", file=sys.stderr)
        return 2

    for rel in shard_files(args.root, args.of)[args.shard - 1]:
        print(rel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
