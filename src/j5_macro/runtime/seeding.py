"""Per-action sub-seeds (contract, "Mouse and keyboard masters": seed per action)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence


def derive_seed(seed: int, step_id: str, iterations: Sequence[int] | None, attempt: int) -> int:
    """A 64-bit sub-seed from the run seed, the step, its loop iterations and the attempt.

    Uses sha256 over a canonical JSON encoding, so it is stable across processes and
    Python versions (unlike ``hash()``).
    """
    text = json.dumps([seed, step_id, list(iterations or ()), attempt], separators=(",", ":"))
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
