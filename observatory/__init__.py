"""observatory — a pluggable observability substrate for AI agents and the systems they touch.

CONTRACT.md is normative; this package is the reference implementation of it.

    from observatory import Event, JsonlLedger, FileCapture, probe, request_key

    led = JsonlLedger("runs/ledger.jsonl", secrets=[token])
    probe(led).raise_if_not_ready()
    led.append(Event(kind="task.start", subject=task_id, grade="seam", source="my-tool"))

    cap = FileCapture("runs/boundary.jsonl", mode="replay")
    reply = cap.through(request_key("POST", path, body), lambda: client.post(path, body))
"""

from .capture import RECORD, REPLAY, FileCapture, request_key
from .contract import (
    GRADES,
    GROUND_TRUTH,
    CaptureMiss,
    Capture,
    Event,
    Ledger,
    ObservatoryError,
    UnknownGrade,
    now_mono,
    now_utc,
)
from .ledger import JsonlLedger
from .probe import Check, ProbeResult, probe
from .redact import REDACTED, Redactor, redact

__all__ = [
    "GRADES", "GROUND_TRUTH", "Event", "Ledger", "Capture",
    "ObservatoryError", "UnknownGrade", "CaptureMiss",
    "now_utc", "now_mono",
    "JsonlLedger", "FileCapture", "request_key", "RECORD", "REPLAY",
    "Redactor", "redact", "REDACTED",
    "probe", "ProbeResult", "Check",
]

__version__ = "0.1.0"
