"""Self-tests for the bundled JSONL implementation.

Run these checks with ``python3 -m observatory.selftest``.

The optional factories are test seams for classes that use the same file helpers.
They do not define a generic ``Ledger`` protocol conformance interface.
Projects must evaluate their implementations against the checklist in CONTRACT.md.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
from collections.abc import Callable
from typing import Any

from . import (
    REDACTED,
    CaptureMiss,
    Event,
    FileCapture,
    JsonlLedger,
    ObservatoryError,
    UnknownGrade,
    probe,
    request_key,
)

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "ok  " if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def main(
    *,
    ledger_factory: Callable[..., Any] = JsonlLedger,
    capture_factory: Callable[..., Any] = FileCapture,
) -> int:
    FAILED.clear()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="observatory-selftest-"))
    print(f"observatory reference self-test: scratch {tmp}\n")

    # -- 1. Events carry both clocks and a grade from the fixed set ---------------
    print("1. Event: clocks and grades")
    e = Event(kind="t", subject="s", grade="seam", source="selftest")
    check(
        "both clocks are populated without the caller supplying them",
        bool(e.at_utc) and isinstance(e.at_mono, float),
        f"at_utc={e.at_utc}",
    )
    try:
        Event(kind="t", subject="s", grade="definitely-not-a-grade", source="selftest")
        check("an unknown grade raises", False, "it was accepted")
    except UnknownGrade:
        check("an unknown grade raises", True)
    check(
        "self_reported is not direct boundary evidence",
        not Event(
            kind="t", subject="s", grade="self_reported", source="x"
        ).is_ground_truth,
    )
    check(
        "seam/native/proxy are direct boundary evidence",
        all(
            Event(kind="t", subject="s", grade=g, source="x").is_ground_truth
            for g in ("seam", "native", "proxy")
        ),
    )
    try:
        Event(kind="", subject="s", grade="seam", source="x")
        check("an empty required field raises", False, "it was accepted")
    except ObservatoryError:
        check("an empty required field raises", True)

    # -- 2. The ledger is append-only, restart-safe, truncation-tolerant ----------
    print("\n2. Ledger: durability")
    led = ledger_factory(tmp / "ledger.jsonl")
    for i in range(3):
        led.append(
            Event(
                kind="k",
                subject=f"s{i}",
                grade="seam",
                source="selftest",
                data={"i": i},
            )
        )
    check("rows read back in order", [x.data["i"] for x in led.read()] == [0, 1, 2])
    check("filtering by subject works", len(list(led.read(subject="s1"))) == 1)

    reopened = ledger_factory(tmp / "ledger.jsonl")  # simulates a process restart
    reopened.append(
        Event(kind="k", subject="s3", grade="seam", source="selftest", data={"i": 3})
    )
    check("a reopened ledger appends rather than truncating", reopened.count() == 4)

    with (tmp / "ledger.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"kind":"k","subject":"trunc"')  # killed mid-write
    check(
        "a truncated final row costs one row, not the file",
        ledger_factory(tmp / "ledger.jsonl").count() == 4,
    )

    # -- 3. A replay miss raises -------------------------------------------------
    print("\n3. Capture: replay fails closed")
    path = tmp / "cap.jsonl"
    rec = capture_factory(path, mode="record")
    key = request_key("POST", "/v1/messages", {"q": 1})
    calls = {"n": 0}

    def thunk():
        calls["n"] += 1
        return {"answer": 42}

    check(
        "record mode calls through and returns the value",
        rec.through(key, thunk) == {"answer": 42} and calls["n"] == 1,
    )

    rep = capture_factory(path, mode="replay")
    check("replay serves the recorded value", rep.through(key, thunk) == {"answer": 42})
    check("replay did NOT call through", calls["n"] == 1, f"thunk calls={calls['n']}")

    try:
        rep.through(request_key("POST", "/v1/messages", {"q": 999}), thunk)
        check("a replay miss raises", False, "it fell through to the live call")
    except CaptureMiss:
        check("a replay miss raises CaptureMiss", True)
    check("the miss still did not call through", calls["n"] == 1)
    check(
        "an identical request derives an identical key",
        request_key("POST", "/x", {"a": 1}) == request_key("POST", "/x", {"a": 1}),
    )
    check(
        "a different request derives a different key",
        request_key("POST", "/x", {"a": 1}) != request_key("POST", "/x", {"a": 2}),
    )

    # -- 4. A planted secret never reaches disk ----------------------------------
    print("\n4. Redaction: the planted-secret test")
    secret = "sk-live-ZZZ9planted9credential9value9do9not9store"
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r"
    )
    sled = ledger_factory(tmp / "secret.jsonl", secrets=[secret])
    sled.append(
        Event(
            kind="call",
            subject="s",
            grade="proxy",
            source="selftest",
            data={
                "authorization": "Bearer " + secret,
                "note": f"the token is {secret}, keep it safe",
                "jwt_in_prose": f"got back {jwt} from the endpoint",
                "nested": {"headers": {"api_key": secret}},
                "harmless": "this text must survive",
            },
        )
    )
    raw = sled.raw_bytes()
    check(
        "the registered literal is absent from stored bytes", secret.encode() not in raw
    )
    check("a JWT in free prose is caught by value shape", jwt.encode() not in raw)
    check("the secret's existence is still recorded", REDACTED.encode() in raw)
    check("non-secret content survives", b"this text must survive" in raw)
    row = json.loads(raw.decode().splitlines()[0])
    check(
        "a secret-named key is scrubbed whatever its value",
        row["data"]["authorization"] == REDACTED,
    )
    check(
        "nested payloads are scrubbed at depth",
        row["data"]["nested"]["headers"]["api_key"] == REDACTED,
    )

    ccap = capture_factory(tmp / "cap2.jsonl", mode="record", secrets=[secret])
    ccap.through(request_key("x"), lambda: {"authorization": secret})
    check(
        "capture redacts too, not just the ledger",
        secret.encode() not in (tmp / "cap2.jsonl").read_bytes(),
    )

    # -- 5. The probe blocks when the ledger cannot round-trip -------------------
    print("\n5. Probe: refuses to certify a broken instrument")
    good = probe(ledger_factory(tmp / "probe-ok.jsonl"))
    check(
        "a working ledger probes ready", good.ready, good.render().replace("\n", " | ")
    )

    broken_dir = tmp / "not-a-file"
    broken_dir.mkdir()
    bad = probe(ledger_factory(broken_dir))  # appending to a directory must fail
    check("an unwritable ledger probes NOT ready", not bad.ready)
    try:
        bad.raise_if_not_ready()
        check("raise_if_not_ready blocks rather than warns", False, "it returned")
    except RuntimeError:
        check("raise_if_not_ready blocks rather than warns", True)

    print()
    if FAILED:
        print(f"SELF-TEST FAILED ({len(FAILED)}): " + "; ".join(FAILED))
        return 1
    print("SELF-TEST PASSED: all checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
