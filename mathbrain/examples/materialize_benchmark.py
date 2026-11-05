"""Materialize a certified synthetic benchmark bank into ignored local storage."""

from __future__ import annotations

import argparse

from math_brain.benchmark_artifacts import materialize_benchmark
from math_brain.models import canonical_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_directory")
    parser.add_argument("--seed", default="public-10000-regression")
    parser.add_argument("--count", type=int, default=10_000)
    parser.add_argument("--start", type=int, default=0)
    args = parser.parse_args()
    artifact = materialize_benchmark(
        args.output_directory,
        seed=args.seed,
        count=args.count,
        start=args.start,
    )
    print(
        canonical_json(
            {
                "directory": str(artifact.directory),
                "item_count": artifact.manifest.item_count,
                "items_bytes": artifact.items_bytes,
                "items_digest": artifact.items_digest,
                "manifest_digest": artifact.manifest.digest(),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
