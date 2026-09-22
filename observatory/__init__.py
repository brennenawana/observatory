"""Observatory provides a generic observability reference implementation.

CONTRACT.md provides implementation guidance. Projects derive their own capture profiles.

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
    Capture,
    CaptureMiss,
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
from .secure_ledger import SecureJsonlLedger

__all__ = [
    "GRADES",
    "GROUND_TRUTH",
    "RECORD",
    "REDACTED",
    "REPLAY",
    "Capture",
    "CaptureMiss",
    "Check",
    "Event",
    "FileCapture",
    "JsonlLedger",
    "Ledger",
    "ObservatoryError",
    "ProbeResult",
    "Redactor",
    "SecureJsonlLedger",
    "UnknownGrade",
    "now_mono",
    "now_utc",
    "probe",
    "redact",
    "request_key",
]

__version__ = "0.2.0"
