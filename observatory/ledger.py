"""Append-only JSONL ledger. CONTRACT.md §2.

JSONL rather than a database, for three reasons that matter more than query speed:

* A half-written final row costs you that row. A corrupted database costs you the file.
  Processes observing agents get killed; the storage format has to expect it.
* It appends across restarts with no coordination, which playbook 12 §5.1 makes a MUST —
  a log that truncates on restart destroys exactly the sessions a reconstruction needs.
* Anything can read it. An observability record nobody can open without your tooling
  has recreated the problem it was built to solve.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any, Iterable, Iterator

from .contract import Event, ObservatoryError
from .redact import Redactor


class JsonlLedger:
    """One file, one Event per line, oldest first.

    ``secrets`` seeds the redactor with strings the caller already knows are
    credentials — a token read from a file, say. See CONTRACT.md §5.
    """

    def __init__(self, path: str | os.PathLike[str], *,
                 secrets: Iterable[str] = (), redactor: Redactor | None = None) -> None:
        self.path = pathlib.Path(path)
        self.redactor = redactor or Redactor(secrets)
        self.appended = 0

    # -- write ------------------------------------------------------------------
    def append(self, event: Event) -> None:
        """Persist one Event. Redacts first, and returns only once it is on disk.

        The flush-and-fsync is not belt-and-braces. `append` returning before the row
        is durable would mean the rows most worth having — the ones written moments
        before a crash — are exactly the ones you lose.
        """
        if not isinstance(event, Event):
            raise ObservatoryError("append() takes an Event")
        row = event.to_dict()
        row["data"] = self.redactor.scrub(row.get("data") or {})
        line = json.dumps(row, ensure_ascii=False, default=str, separators=(",", ":"))
        if "\n" in line:  # defensive: a newline inside a row would split it in two
            line = line.replace("\n", "\\n")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self.appended += 1

    # -- read -------------------------------------------------------------------
    def read(self, *, subject: str | None = None, kind: str | None = None,
             since: str | None = None) -> Iterator[Event]:
        """Iterate stored Events, oldest first.

        A truncated or unparseable final row is skipped rather than raised. A process
        killed mid-write must cost one row, not the whole record.
        """
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue  # partial write; the rest of the ledger is still good
                if not isinstance(row, dict):
                    continue
                if subject is not None and row.get("subject") != subject:
                    continue
                if kind is not None and row.get("kind") != kind:
                    continue
                if since is not None and str(row.get("at_utc", "")) < since:
                    continue
                try:
                    yield Event.from_dict(row)
                except Exception:
                    continue  # a row we cannot rebuild is not a reason to stop reading

    def raw_bytes(self) -> bytes:
        """Everything on disk. Used by the planted-secret conformance check."""
        return self.path.read_bytes() if self.path.exists() else b""

    def count(self) -> int:
        return sum(1 for _ in self.read())
