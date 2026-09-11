# observatory

A pluggable observability substrate for AI agents and the systems they touch.

Watching an agent work is its own system. It has uptime, storage that grows, retention
rules, and it breaks every time a harness changes its hook format. That is why this is a
separate repository rather than a folder inside the tool it serves: it has its own
lifecycle, and it will outlive any one consumer.

It is deliberately small, has **no dependencies**, and is stdlib-only Python 3.10+, so it
can drop into whatever is already running — Claude Code, OpenCode, Codex, a local model
behind an endpoint, or a pipeline of your own.

## The contract is the product

**[CONTRACT.md](CONTRACT.md) is the primary artifact.** The code here is a reference
implementation of it.

A tool that needs to observe itself programs against the contract, not against this
package. Anyone who prefers their own recorder implements the same contract and swaps it
in. That substitution is the whole design: a tool that hard-codes one recorder has picked
the observability system for every future user of it.

The contract was not invented. Two implementations built months apart for unrelated
problems — an agent coach and a data-ingest rig — independently arrived at the same six
operations: append to a durable record, capture at a boundary, replay failing closed on a
miss, grade the evidence, redact at write time, and prove the instrument before trusting
it. CONTRACT.md writes those down and says why each one is shaped the way it is.

## Where the methodology lives

The *principles* are not here. They are in the Adaptive Systems Playbook, chapter 12:

- `12 §5.1` — the telemetry floor: what must be recording before a long or expensive run.
- `12 §5.2` — the minimum trajectory record.
- `12 §5.3` — how much to record, and who the record is for.

This repository **cites those sections and implements them. It never paraphrases them.**
A paraphrase drifts from its source, and then two documents disagree about what the rule
is — which is a failure this project has already had to clean up once elsewhere.

## Quick start

```python
from observatory import Event, JsonlLedger, FileCapture, probe, request_key

led = JsonlLedger("runs/ledger.jsonl", secrets=[my_token])
probe(led).raise_if_not_ready()      # blocks, rather than warns, if it cannot record

led.append(Event(
    kind="task.start", subject=task_id, grade="seam", source="my-tool",
    data={"prompt_tokens": 812},
))

cap = FileCapture("runs/boundary.jsonl", mode="replay")
reply = cap.through(
    request_key("POST", "/v1/messages", body),
    lambda: client.post("/v1/messages", body),   # never called in replay mode
)
```

Run the conformance checks:

```
python3 -m observatory.selftest
```

## Three things worth knowing before you use it

**A replay miss raises.** It does not fall through to the live call. That is what makes
"this run stayed offline" a proof instead of a hope — and the exception type is a plain
`RuntimeError` subclass on purpose, so a consuming tool that catches its own provider
errors cannot swallow it and silently turn a replay into a live call.

**`self_reported` is not ground truth.** An agent's account of itself is evidence about
what it *says* it did. The grade travels with every row so a finding built on it can be
labelled honestly — "the agent reports it read the file", never "the agent read the file".

**Redaction happens before the write, not after.** A secret's existence is recorded; its
value never is. The conformance suite plants a real-shaped credential and asserts it is
absent from the stored bytes, because redaction that is merely believed to work is the
highest-consequence failure in the whole contract.

## Status

Early. What is here works and is covered by 27 conformance checks; what is not here is
listed honestly rather than implied.

**Built:** the contract; the reference ledger, capture, redactor and probe; the
conformance suite.

**Not built yet:**

- **Adapters.** Harness hooks and an endpoint proxy (the agent-facing side), and code-seam
  wrapping (the system-facing side). The contract is designed for both; neither ships yet.
- **The extraction proof.** The real test of whether this abstraction is honest is whether
  it can replace a working recorder without changing its results. The candidate is a data
  ingest rig with an exact target to hit — a frozen funnel fingerprint and a 586-entry
  cassette that replays with the network cut.
- **Value-level provenance.** Which component produced a number, and what it fell back
  from. A declared extension point in CONTRACT.md §3, not yet specified.
- **A second consumer.** [wikiskills-lab](https://github.com/brennenawana/wikiskills-lab)
  has its own recorder today. It becomes consumer number two by programming against the
  contract and keeping that recorder as one satisfying implementation — which is also what
  demonstrates that the substitution actually works.

Two consumers that different, hitting one core, is what keeps an abstraction honest. One
consumer produces a library shaped like exactly one caller.
