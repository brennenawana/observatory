"""Generic contract types and vocabularies. CONTRACT.md provides implementation guidance.

This module deliberately holds no I/O and no policy. It is the part another
implementation imports (or re-declares) when it wants to satisfy the same contract
without using this repository's ledger and capture.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

#: How we know a thing happened. CONTRACT.md §3. Fixed on purpose: a grade that can be
#: invented at the call site launders a guess into a measurement.
GRADES: tuple[str, ...] = ("native", "proxy", "seam", "self_reported")

#: Legacy name for grades that provide direct evidence of a boundary event.
#: These grades do not prove every assertion inside an event payload.
GROUND_TRUTH: frozenset[str] = frozenset({"native", "proxy", "seam"})


class ObservatoryError(RuntimeError):
    """Base class. A plain RuntimeError subclass on purpose — see CaptureMiss."""


class UnknownGrade(ObservatoryError):
    """A grade outside GRADES was supplied. Never stored, always raised."""


class CaptureMiss(ObservatoryError):
    """Replay was asked for a boundary crossing that was never recorded.

    This is the contract's load-bearing failure. It is a RuntimeError rather than
    a transport or provider error. A consumer can catch those errors and use a live
    fallback. A distinct error prevents a replay miss from becoming an unreported
    live call.
    """


def now_utc() -> str:
    """Realtime, ISO 8601, UTC, with an explicit offset."""
    return datetime.now(timezone.utc).isoformat()


def now_mono() -> float:
    """Monotonic, for durations. Comparable only inside one process (CONTRACT.md §1)."""
    return time.monotonic()


@dataclass(frozen=True)
class Event:
    """One thing that happened. CONTRACT.md §1.

    Both clocks are captured at construction rather than accepted as arguments, so a
    caller cannot omit one. A single-clock recorder cannot detect wall-clock changes
    while it measures elapsed time.
    """

    kind: str
    subject: str
    grade: str
    source: str
    data: dict[str, Any] = field(default_factory=dict)
    at_utc: str = field(default_factory=now_utc)
    at_mono: float = field(default_factory=now_mono)

    def __post_init__(self) -> None:
        if self.grade not in GRADES:
            raise UnknownGrade(
                f"grade {self.grade!r} is not one of {GRADES}. "
                "An unrecognized grade is refused rather than defaulted: a row whose "
                "provenance is a guess must not be storable."
            )
        for name in ("kind", "subject", "source"):
            if not str(getattr(self, name) or "").strip():
                raise ObservatoryError(
                    f"Event.{name} is required and must be non-empty"
                )

    @property
    def is_ground_truth(self) -> bool:
        """Use the legacy name for direct boundary evidence."""
        return self.grade in GROUND_TRUTH

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> Event:
        """Rebuild from a stored row, preserving its original clocks."""
        known = {"kind", "subject", "grade", "source", "data", "at_utc", "at_mono"}
        return cls(**{k: v for k, v in row.items() if k in known})


@runtime_checkable
class Ledger(Protocol):
    """CONTRACT.md §2. Append-only, durable on return, restart-safe."""

    def append(self, event: Event) -> None: ...

    def read(
        self,
        *,
        subject: str | None = None,
        kind: str | None = None,
        since: str | None = None,
    ) -> Iterable[Event]: ...


@runtime_checkable
class Capture(Protocol):
    """CONTRACT.md §4. Record or replay a boundary crossing; a replay miss raises."""

    mode: str

    def through(self, key: str, thunk: Any) -> Any: ...
