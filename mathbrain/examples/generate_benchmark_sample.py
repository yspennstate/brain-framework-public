"""Print a small deterministic benchmark sample and its manifest as canonical JSON."""

from __future__ import annotations

import argparse

from math_brain.benchmark import build_manifest, generate_bank
from math_brain.models import canonical_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", default="math-brain-public-sample-v1")
    parser.add_argument("--count", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.count <= 100:
        parser.error("sample count must be between 1 and 100")

    items = list(generate_bank(args.seed, args.count))
    payload = {
        "items": [item.to_dict() for item in items],
        "manifest": build_manifest(items).to_dict(),
    }
    print(canonical_json(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
