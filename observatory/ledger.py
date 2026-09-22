"""Append-only JSONL ledger. CONTRACT.md §2.

JSONL rather than a database, for three reasons that matter more than query speed:

* A half-written final row costs you that row. A corrupted database costs you the file.
  Processes observing agents get killed; the storage format has to expect it.
* It appends across restarts. A log that truncates on restart loses prior sessions.
* Anything can read it. An observability record nobody can open without your tooling
  has recreated the problem it was built to solve.
"""

from __future__ import annotations

import json
import os
import pathlib
from collections.abc import Iterable, Iterator

from .contract import Event, ObservatoryError
from .redact import Redactor


def _fsync_directory(path: pathlib.Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        directory_fd = os.open(path, flags)
    except OSError as exc:
        raise ObservatoryError(
            "Could not open a directory for durable storage"
        ) from exc
    try:
        os.fsync(directory_fd)
    except OSError as exc:
        raise ObservatoryError("Could not synchronize a directory") from exc
    finally:
        os.close(directory_fd)


def _create_parent_directories(path: pathlib.Path) -> None:
    missing: list[pathlib.Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        _fsync_directory(directory.parent)


def _encode_event(event: Event, redactor: Redactor) -> bytes:
    try:
        row = event.to_dict()
        row["data"] = redactor.scrub(row.get("data") or {})
        line = json.dumps(
            row,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ObservatoryError(
            "Event data must contain JSON-compatible values"
        ) from exc
    if "\n" in line:
        line = line.replace("\n", "\\n")
    return (line + "\n").encode("utf-8")


class JsonlLedger:
    """One file, one Event per line, oldest first.

    This reference ledger assumes that the path and its parent directories are trusted.
    Use ``SecureJsonlLedger`` for private local records when another process can replace
    path components.

    ``secrets`` seeds the redactor with strings the caller already knows are
    credentials. See CONTRACT.md §5.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        secrets: Iterable[str] = (),
        redactor: Redactor | None = None,
    ) -> None:
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
        encoded = _encode_event(event, self.redactor)
        _create_parent_directories(self.path.parent)
        created = False
        flags = os.O_APPEND | os.O_RDWR
        try:
            file_descriptor = os.open(self.path, flags | os.O_CREAT | os.O_EXCL, 0o666)
            created = True
        except FileExistsError:
            file_descriptor = os.open(self.path, flags)
        with os.fdopen(file_descriptor, "a+b") as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell() > 0:
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) != b"\n":
                    fh.write(b"\n")
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        if created:
            _fsync_directory(self.path.parent)
        self.appended += 1

    # -- read -------------------------------------------------------------------
    def read(
        self,
        *,
        subject: str | None = None,
        kind: str | None = None,
        since: str | None = None,
    ) -> Iterator[Event]:
        """Iterate stored Events, oldest first.

        A truncated or unparseable final row is skipped rather than raised. A process
        killed mid-write must cost one row, not the whole record.
        """
        if not self.path.exists():
            return
        with self.path.open("rb") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
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
                except (ObservatoryError, TypeError):
                    continue  # a row we cannot rebuild is not a reason to stop reading

    def raw_bytes(self) -> bytes:
        """Return stored bytes for the planted-secret self-test."""
        return self.path.read_bytes() if self.path.exists() else b""

    def count(self) -> int:
        return sum(1 for _ in self.read())
