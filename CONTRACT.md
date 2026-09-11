# The observability contract

This is the primary artifact in this repository. The code under `observatory/` is a
reference implementation of it, not the definition of it.

A tool that needs to watch itself — [wikiskills-lab](https://github.com/brennenawana/wikiskills-lab)
is the first — programs against this contract. Anyone who prefers their own recorder
implements the same contract and swaps it in. That substitution is the point; a tool
that hard-codes one recorder has chosen its observability system for every future user.

## Where this comes from

Not invented. Two independent implementations, built months apart for different
problems, arrived at the same five operations:

| Operation | wikiskills-lab | the crexi ingest rig |
|---|---|---|
| Append to a durable record | `engine/journal.py` | per-asset JSONL trace |
| Capture at a boundary | `engine/recorder/proxy.py`, `hooks/` | seam-wrapping in `rig/trace.py` |
| Replay, failing closed on a miss | proxy replay mode | `rig/cassette.py` → `CassetteMiss` |
| Grade the evidence | telescopes A / A / C | `rig/provenance.py` producer vocabulary |
| Prove the instrument before trusting it | `engine/recorder/probe.py` + calibration | preflight + counter reconciliation |

Both also redact at write time. Two people solving unrelated problems built the same
six things, so these are the operations, and this document just writes them down.

The methodology behind them is not restated here. It lives in the Adaptive Systems
Playbook, chapter 12 — `§5.1` the telemetry floor, `§5.2` the minimum trajectory record,
`§5.3` choosing depth and audience. **This repository cites those sections and
implements them. It never paraphrases them**, because a paraphrase drifts from its
source and then two documents disagree about what the rule is.

---

## 1. Event

The unit of record. One thing that happened.

| Field | Required | Meaning |
|---|---|---|
| `at_utc` | yes | Realtime stamp, ISO 8601, UTC. |
| `at_mono` | yes | Monotonic stamp from the same process. |
| `kind` | yes | What sort of thing happened. Free vocabulary, owned by the caller. |
| `subject` | yes | What it is about — a session, a task, an asset id. The join key. |
| `grade` | yes | How we know. From §3's fixed set. |
| `source` | yes | Which component wrote the row. |
| `data` | yes | Everything else. Redacted before it is persisted (§5). |

**Both clocks are required, and this is not negotiable.** Playbook `12 §5.1` puts dual
clocks on the telemetry floor: virtualization and power management skew a realtime clock,
and the skew stays invisible until something cross-checks the two. A recorder that writes
one clock cannot detect the defect it exists to make visible.

`at_mono` is only comparable **within one process**. Across processes it orders nothing.
An implementation MUST NOT present cross-process monotonic differences as durations.

## 2. Ledger

An append-only sequence of Events.

- `append(event)` — persist one Event. MUST redact first (§5).
- `read(subject=None, kind=None, since=None)` — iterate Events, oldest first.

Rules:

- **Append-only.** No update, no delete in the interface. A record that can be edited
  after the fact cannot settle a disagreement about what happened, which is the only
  reason it exists.
- **Durable before returning.** `append` returns after the row is on disk, not after it
  is queued. A crash is exactly when the record matters most.
- **Survives restarts.** Playbook `12 §5.1` makes this a MUST: a log that truncates on
  restart destroys precisely the sessions a forensic reconstruction needs.
- **Partial reads are legal.** A truncated final row is skipped, not fatal. A process
  killed mid-write must not poison the whole ledger.

## 3. Grade — how we know

Every Event carries one value from this fixed set. An unknown value MUST raise rather
than be stored, because a grade that silently defaults is worse than no grade: it
launders a guess into a measurement.

| Grade | What produced it | Ground truth? |
|---|---|---|
| `native` | The tool emitted it itself — hooks, event logs, transcripts. | Yes |
| `proxy` | We intercepted the boundary it crossed — an endpoint, an HTTP call. | Yes |
| `seam` | We wrapped the code path in-process. | Yes |
| `self_reported` | The observed agent described its own action. | **No** |

`self_reported` is the load-bearing distinction. An agent's account of itself is
evidence about what it *says* it did. Any finding resting on it MUST be presented that
way — "the agent reports it read the file", never "the agent read the file". A consumer
that cannot show grades in its output should not accept `self_reported` rows at all.

A future grade for value-level provenance — which component produced a number, and what
it fell back from — is a declared extension point, not yet specified here. The crexi
rig's producer vocabulary is the input for that work.

## 4. Capture — record and replay a boundary

A boundary is anywhere the system reaches something it does not control: a model
endpoint, an HTTP API, a clock, a database.

- `through(key, thunk)` in **record** mode: call `thunk()`, store the result under
  `key`, return it.
- `through(key, thunk)` in **replay** mode: return the stored result for `key`. If there
  is none, **raise**. Never call `thunk`.

**The miss MUST raise.** This is the single rule that separates a proof from an
assumption. A recorder that quietly falls through to the real call on a miss cannot tell
you whether a replay actually stayed offline — and the failure is silent, so you find out
by publishing a number that was measured against a live service you thought you had cut.
Both reference implementations raise, and both were verified by pointing the base URL at
an unreachable address and confirming the run still completed from the recording alone.

The raise MUST be an exception type the calling tool does not already swallow. In
wikiskills-lab this was decisive: the LLM path catches its own provider errors and falls
back a tier, so a miss surfacing as a provider error would have been invisible —
indistinguishable from the no-model arm.

`key` MUST be derived from the *request*, never from a caller-supplied label. Two
identical requests are the same question. A key built from an id would serve a stale
answer after the request changed.

## 5. Redaction — at write time, never after

- `redact(data)` runs **before** anything is persisted.
- A secret's **existence** may be recorded. Its **value** never is.

Retroactive redaction is not a substitute. Once a value is on disk it is on disk, and
every backup and replica took it with them.

An implementation MUST be tested against a planted secret: write a known credential into
an event, then assert that the value cannot be found anywhere in the stored bytes. That
test is not optional, because redaction that is merely believed to work is the single
highest-consequence failure in this whole contract.

## 6. Probe — prove the instrument before trusting it

Playbook `00` P2: the instrument outranks the score. A record nobody has verified is
being collected is not evidence of anything.

- `probe()` returns a result with `ready: bool` and a list of named checks.
- `ready` false MUST block the work that depends on the record, not merely warn.

A probe answers one question: if something happened right now, would this ledger show
it? The reference implementation writes a canary event, reads it back, and confirms
redaction held on the way through.

---

## Conformance

An implementation conforms when it satisfies, in order:

1. Events carry both clocks and a grade from the fixed set; an unknown grade raises.
2. The ledger is append-only and survives a restart and a truncated final row.
3. A replay miss raises, with an exception the consuming tool does not swallow.
4. A planted secret does not appear in stored bytes.
5. A probe reports `ready` false when the ledger cannot round-trip an event.

`observatory/selftest.py` checks all five against the reference implementation. A
different implementation should be able to run the same checks against itself.
