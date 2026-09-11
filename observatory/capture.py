"""Record and replay a boundary crossing. CONTRACT.md §4.

A boundary is anywhere the system reaches something it does not control: a model
endpoint, an HTTP API, a database, a clock. Freeze those and a re-run measures your
change instead of the world's drift.

The one rule that makes this worth having: **in replay, a miss raises.** A recorder that
falls through to the live call on a miss cannot tell you whether a replay stayed offline.
Worse, it cannot tell you it failed — you find out when you publish a number that was
quietly measured against a live service you believed you had disconnected.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
from typing import Any, Callable, Iterable

from .contract import CaptureMiss, ObservatoryError
from .redact import Redactor

RECORD = "record"
REPLAY = "replay"


def request_key(*parts: Any) -> str:
    """Derive a key from the request itself.

    Deliberately not a caller-supplied label. Two identical requests are the same
    question and should share an answer; a key built from an id would keep serving a
    stale answer after the request changed, which is the failure that makes a frozen
    corpus lie rather than merely go stale.
    """
    blob = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


class FileCapture:
    """A JSONL-backed keyed store with record and replay modes."""

    def __init__(self, path: str | os.PathLike[str], mode: str = REPLAY, *,
                 secrets: Iterable[str] = (), redactor: Redactor | None = None) -> None:
        if mode not in (RECORD, REPLAY):
            raise ObservatoryError(f"mode must be {RECORD!r} or {REPLAY!r}, got {mode!r}")
        self.path = pathlib.Path(path)
        self.mode = mode
        self.redactor = redactor or Redactor(secrets)
        self.hits = 0
        self.misses = 0
        self.recorded = 0
        self._entries: dict[str, Any] = {}
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row, dict) and "key" in row:
                        self._entries[row["key"]] = row.get("value")

    def __len__(self) -> int:
        return len(self._entries)

    def through(self, key: str, thunk: Callable[[], Any]) -> Any:
        """Serve ``key`` from the store, or record what ``thunk()`` returns.

        In replay mode ``thunk`` is never called — not on a miss, not as a fallback.
        That is the whole guarantee.
        """
        if key in self._entries:
            self.hits += 1
            return self._entries[key]
        if self.mode == REPLAY:
            self.misses += 1
            raise CaptureMiss(
                f"no recording for key {key}. The corpus is incomplete for this run. "
                "Re-record deliberately; this call will not reach the network."
            )
        value = thunk()
        self._append(key, value)
        return value

    def _append(self, key: str, value: Any) -> None:
        row = {"key": key, "value": self.redactor.scrub(value)}
        self._entries[key] = row["value"]
        self.recorded += 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def summary(self) -> dict[str, Any]:
        """What a run should print so 'it stayed offline' is shown, not assumed."""
        return {"mode": self.mode, "entries": len(self._entries), "hits": self.hits,
                "misses": self.misses, "recorded": self.recorded, "path": str(self.path)}
