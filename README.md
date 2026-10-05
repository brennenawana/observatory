# observatory

Observatory defines a generic observability contract for AI agents and other automated systems.

The repository has no agent-runtime or IDE dependency.

## The contract is the product

[CONTRACT.md](https://github.com/brennenawana/observatory/blob/main/CONTRACT.md) contains implementation guidance. The Python package provides one reference implementation.

Each project can define its own event vocabulary and capture policy. It does not need to copy one universal schema. See [capture profiles](https://github.com/brennenawana/observatory/blob/main/docs/CAPTURE_PROFILES.md).

The contract defines six common operations:

1. Append events to a durable ledger.
2. Capture calls at system boundaries.
3. Fail closed when replay data is missing.
4. State the evidence grade.
5. Redact data before storage.
6. Test the recorder before trusting its output.

## Install

```bash
python3 -m pip install .
```

The distribution name is `agent-observatory-contract`. The Python import name remains `observatory`.

Observatory supports Python 3.10 and later. The reference package uses only the Python standard library.

## Quick start

```python
from observatory import Event, JsonlLedger, probe

ledger = JsonlLedger("runs/ledger.jsonl", secrets=[my_token])
probe(ledger).raise_if_not_ready()
ledger.append(
    Event(
        kind="task.start",
        subject=task_id,
        grade="seam",
        source="my-tool",
        data={"prompt_tokens": 812},
    )
)
```

Use `JsonlLedger` only when you trust the local path and its parent directories.

Use `SecureJsonlLedger` for private local records on supported POSIX systems:

```python
from observatory import SecureJsonlLedger

ledger = SecureJsonlLedger(
    "~/.local/share/my-observer",
    relative_dir=("runs", run_id_hash),
    filename="events.jsonl",
)
```

`SecureJsonlLedger` uses private directory and file modes. It rejects link replacement. It refuses to start when the required secure file operations are unavailable.

## Optional integrations

Observatory does not select an agent runtime or IDE.

- [Integration design](https://github.com/brennenawana/observatory/blob/main/docs/integrations/README.md) explains how adapters connect a host to the contract.
- [IDE and terminal agents](https://github.com/brennenawana/observatory/blob/main/docs/integrations/IDE_AGENTS.md) covers VS Code, Cursor, Claude Code, and direct terminal agents.
- If you use Hermes, see the [optional setup recommendation](https://github.com/brennenawana/observatory/blob/main/docs/integrations/HERMES.md).

These documents are recommendations. They are not core requirements.

## Companion guidebook

[COMPANION.md](https://github.com/brennenawana/observatory/blob/main/COMPANION.md) describes the separate `agent-observability-guidebook` repository. The guidebook applies this contract to coding agents to track spend and identify optimization opportunities. The companion document also describes the relationship between the repositories, an agent reading path, and example prompts.

## Verify

```bash
python3 -m unittest -v
python3 -m observatory.selftest
```

The module tests the bundled JSONL reference implementation. It is not a generic `Ledger` protocol conformance runner.

Projects must evaluate their implementations against [CONTRACT.md](https://github.com/brennenawana/observatory/blob/main/CONTRACT.md). Adapter repositories must also test their event profiles.

## Important behavior

### Replay misses raise

A replay miss does not call the live service. This behavior proves that an offline replay stayed offline.

### `self_reported` is not direct boundary evidence

An agent report is evidence about what the agent reports. It is not native evidence of the action.

### Redaction occurs before storage

A secret value must not reach the ledger. The reference self-tests use a planted credential to test this rule.

## Status

The repository includes:

- the generic contract;
- a reference event and ledger implementation;
- boundary capture and replay;
- write-time redaction;
- readiness probes;
- a private local JSONL ledger;
- reference implementation self-tests.

The repository does not include a universal agent schema. It does not include a required agent or IDE adapter.
