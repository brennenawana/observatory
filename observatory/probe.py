"""Prove the instrument before trusting the record. CONTRACT.md §6.

Playbook 00, principle P2: the instrument outranks the score. A ledger nobody has
verified is collecting is not evidence of anything — and the way you discover that is
always the same, always late: somebody asks a question the record should answer, and it
is empty for a reason nobody noticed weeks ago.

So the probe answers one question. If something happened right now, would this ledger
show it? ``ready`` false must **block** the work that depends on the record. A warning
gets read once and then stops being read.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from .contract import Event
from .ledger import JsonlLedger
from .redact import REDACTED


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class ProbeResult:
    checks: list[Check] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return all(c.ok for c in self.checks)

    def render(self) -> str:
        lines = [f"  [{'ok' if c.ok else 'FAIL'}] {c.name}"
                 + (f" — {c.detail}" if c.detail else "") for c in self.checks]
        lines.append(f"  ready: {self.ready}")
        return "\n".join(lines)

    def raise_if_not_ready(self) -> None:
        if not self.ready:
            failed = ", ".join(c.name for c in self.checks if not c.ok)
            raise RuntimeError(
                f"observatory probe not ready ({failed}). Refusing to proceed: a run "
                "whose recorder is unverified produces a record nobody can rely on."
            )


def probe(ledger: JsonlLedger) -> ProbeResult:
    """Write a canary, read it back, and confirm redaction held on the way through."""
    result = ProbeResult()
    canary = f"probe-{uuid.uuid4().hex[:12]}"
    planted = f"sk-probe-{uuid.uuid4().hex}"  # looks like an issued key on purpose

    try:
        ledger.redactor.add_literal(planted)
        ledger.append(Event(
            kind="probe.canary", subject=canary, grade="seam", source="observatory.probe",
            data={"canary": canary, "authorization": planted, "note": f"token {planted}"},
        ))
        result.checks.append(Check("ledger accepts an append", True))
    except Exception as exc:  # noqa: BLE001 — the failure is the result
        result.checks.append(Check("ledger accepts an append", False, f"{type(exc).__name__}: {exc}"))
        return result

    rows = [e for e in ledger.read(subject=canary)]
    result.checks.append(Check(
        "the appended event reads back", bool(rows),
        "" if rows else "nothing came back for the canary subject",
    ))

    if rows:
        row = rows[0]
        result.checks.append(Check(
            "both clocks present",
            bool(row.at_utc) and isinstance(row.at_mono, (int, float)),
            f"at_utc={row.at_utc!r}",
        ))
        result.checks.append(Check(
            "grade survives the round trip", row.grade == "seam", f"grade={row.grade!r}",
        ))

    raw = ledger.raw_bytes()
    leaked = planted.encode() in raw
    result.checks.append(Check(
        "planted secret is absent from stored bytes", not leaked,
        "the credential reached disk — redaction is not working" if leaked else "",
    ))
    result.checks.append(Check(
        "the secret's existence is still recorded", REDACTED.encode() in raw,
        "" if REDACTED.encode() in raw else "no redaction marker found",
    ))
    return result
