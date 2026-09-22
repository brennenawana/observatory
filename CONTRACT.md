# The observability contract

This is the primary artifact in this repository. It provides generic implementation
guidance. The code under `observatory/` is a reference implementation.

A project derives its own capture profile from this contract. The project owns its event
names, fields, correlation rules, storage policy, and retention policy. Projects do not
need one universal schema.

Consumers use this contract through replaceable implementations. A consumer must not
require one recorder for all users.

---

## 1. Event

The unit of record. One thing that happened.

| Field | Required | Meaning |
|---|---|---|
| `at_utc` | yes | Realtime stamp, ISO 8601, UTC. |
| `at_mono` | yes | Monotonic stamp from the same process. |
| `kind` | yes | What sort of thing happened. Free vocabulary, owned by the caller. |
| `subject` | yes | The session, task, asset, or other join key. |
| `grade` | yes | How we know. From §3's fixed set. |
| `source` | yes | Which component wrote the row. |
| `data` | yes | Everything else. Redacted before it is persisted (§5). |

**Both clocks are required.** Realtime clocks can change during a run. A monotonic clock
provides stable elapsed-time measurements within one process.

`at_mono` is only comparable **within one process**. Across processes it orders nothing.
An implementation MUST NOT present cross-process monotonic differences as durations.

## 2. Ledger

An append-only sequence of Events.

- `append(event)`: Persist one Event. MUST redact first (§5).
- `read(subject=None, kind=None, since=None)`: Iterate Events, oldest first.

Rules:

- **Append-only.** No update, no delete in the interface. A record that can be edited
  after the fact cannot settle a disagreement about what happened, which is the only
  reason it exists.
- **Durable before returning.** `append` returns after the row is on disk, not after it
  is queued. A crash is exactly when the record matters most. When an append creates a
  file or directory entry, it MUST also synchronize the containing directory.
- **Survives restarts.** A restart must not truncate earlier records.
- **Partial reads are legal.** A truncated final row is skipped, not fatal. A process
  killed mid-write must not poison the whole ledger.
- **A later append starts a new row.** It must not attach valid data to an incomplete
  final row.

## 3. Grade: how we know

Every Event carries one value from this fixed set. An unknown value MUST raise rather
than be stored, because a grade that silently defaults is worse than no grade: it
launders a guess into a measurement.

| Grade | What produced it | Direct evidence of the boundary event? |
|---|---|---|
| `native` | The tool emitted it itself: hooks, event logs, or transcripts. | Yes |
| `proxy` | We intercepted an endpoint or protocol boundary. | Yes |
| `seam` | We wrapped the code path in-process. | Yes |
| `self_reported` | The observed agent described its own action. | **No** |

`self_reported` is the load-bearing distinction. An agent's account of itself is
evidence about what it *says* it did. Any finding resting on it MUST be presented that
way. Use "the agent reports it read the file," not "the agent read the file." A consumer
that cannot show grades in its output should not accept `self_reported` rows at all.

A direct grade proves that the source emitted the recorded boundary event. It does not
prove every claim inside the payload. A profile must define which fields it trusts.

A future grade for value-level provenance is a declared extension point. This contract
does not define that grade.

## 4. Capture: record and replay a boundary

A boundary is anywhere the system reaches something it does not control: a model
endpoint, an HTTP API, a clock, a database.

- `through(key, thunk)` in **record** mode: call `thunk()`, store the result under
  `key`, return it.
- `through(key, thunk)` in **replay** mode: return the stored result for `key`. If there
  is none, **raise**. Never call `thunk`.

**The miss MUST raise.** This is the single rule that separates a proof from an
assumption. A recorder that quietly falls through to the real call on a miss cannot tell
you whether a replay stayed offline. The failure is silent, so you find out
by publishing a number that was measured against a live service you thought you had cut.
The raise MUST be an exception type the calling tool does not already swallow. In
some systems, provider errors cause a fallback. A replay miss must remain distinct from
those errors.

`key` MUST be derived from the *request*, never from a caller-supplied label. Two
identical requests are the same question. A key built from an id would serve a stale
answer after the request changed.

## 5. Redaction: at write time, never after

- `redact(data)` runs **before** anything is persisted.
- A secret's **existence** may be recorded. Its **value** never is.
- A secret-named field redacts its value regardless of the value type.
- A writer rejects non-JSON values. It does not stringify them before redaction.

Retroactive redaction is not a substitute. Once a value is on disk it is on disk, and
every backup and replica took it with them.

An implementation MUST be tested against a planted secret: write a known credential into
an event, then assert that the value cannot be found anywhere in the stored bytes. That
test is not optional, because redaction that is merely believed to work is the single
highest-consequence failure in this whole contract.

## 6. Probe: prove the instrument before trusting it

A record is not usable evidence until a probe verifies the recorder.

- `probe()` returns a result with `ready: bool` and a list of named checks.
- `ready` false MUST block each claim or decision that depends on the record.
- A shadow recorder MAY let unrelated work continue. It MUST report the record as
  unavailable and MUST NOT certify the run as complete.

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

`observatory/selftest.py` tests selected requirements against the bundled JSONL
implementation. It does not prove that a consuming tool propagates `CaptureMiss`.
Other implementations must use this checklist and implementation-specific tests.

---

## 7. Capture profiles

A capture profile applies this contract to one project or host. It MUST define:

- its scope and version;
- its event vocabulary;
- its join keys;
- the evidence grade for each source;
- allowed and excluded data fields;
- completeness rules;
- its storage threat model;
- retention and deletion authority.

A profile MUST classify values before storage. Fixed enums, finite numbers, Booleans,
opaque identifiers, content, and credentials need different treatment. Arbitrary strings
MUST NOT become safe only because an implementation calls them metadata.

An opaque identifier SHOULD use a keyed and domain-separated hash. Hash the complete
value. The profile MUST define key scope, key rotation, and correlation limits. Content
and credential values MUST follow §5.

See `docs/CAPTURE_PROFILES.md` for profile design guidance.

## 8. Completeness

Readiness and completeness are different properties.

- `ready` means the recorder passed its startup probe.
- `complete` means it captured every event required by its profile.
- `incomplete` means it detected a dropped event, write error, missing required event, or
  missing required join.
- `unavailable` means it could not start safely.

A fail-open adapter MAY keep the observed system running. It MUST mark its evidence as
incomplete or unavailable. It MUST NOT turn missing evidence into a successful result.

## 9. Storage threat models

An implementation MUST declare what its ledger protects against.

The reference `JsonlLedger` assumes a trusted local path. It does not protect against a
hostile process that replaces the ledger or a parent directory.

The reference `SecureJsonlLedger` uses descriptor-relative and no-follow file operations.
It uses private permissions and rejects link or inode replacement. It refuses to start
when the required file operations are unavailable.

A project can use another ledger. Its profile MUST state the different guarantees.
