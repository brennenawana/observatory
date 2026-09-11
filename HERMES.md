# Hermes metadata shadow recorder

This adapter records Hermes execution metadata in a local Observatory ledger. It does not add a model-visible tool. It does not change agent decisions.

This adapter is the first integration milestone. It is not a strict evaluation runner.

## Capture policy

The adapter records these data types:

- SHA-256 hashes of session, turn, request, task, model, provider, skill, and tool identifiers;
- hook event kinds and dual timestamps;
- fixed status enums and safe tool-family classifications;
- token usage supplied by Hermes;
- durations, retry counts, result states, and payload sizes;
- skill lifecycle metadata;
- Kanban lifecycle metadata;
- hashed tool names and safe tool-family classifications;
- `qmd`, `gitnexus`, GitHub snapshot, and sprint-plan validation classifications;
- per-process sequence numbers and event identifiers.

The adapter does not persist these data types:

- prompt text;
- model response text;
- conversation history;
- tool argument values;
- tool result values;
- shell commands;
- source code;
- changed file paths;
- error text;
- approval commands or descriptions;
- Kanban summaries or blocked reasons;
- secrets or credential material.

Raw values enter the hook callback because Hermes supplies them. The adapter hashes opaque strings or reduces them to bounded counts and fixed enums before it queues an event. The background queue never stores the raw hook payload.

## Event profile

Each event uses the Observatory `Event` envelope from `CONTRACT.md`.

The adapter adds this profile under `data`:

```json
{
  "event_schema": "hermes-observatory.v1",
  "source_schema": "hermes.observer.v1",
  "capture_policy": "metadata",
  "event_id": "opaque identifier",
  "process_id": 123,
  "process_start_id": "opaque identifier",
  "process_sequence": 1,
  "run_id_hash": "optional managed-run SHA-256 hash"
}
```

Hermes hook events use evidence grade `native`. The event vocabulary belongs to this adapter. It can change independently from another project's event vocabulary.

## Hook mapping

| Hermes hook | Observatory event kind |
|---|---|
| `on_session_start` | `hermes.session.start` |
| `pre_llm_call` | `hermes.turn.start` |
| `pre_api_request` | `hermes.model.request.start` |
| `post_api_request` | `hermes.model.request.finish` |
| `api_request_error` | `hermes.model.request.error` |
| `post_tool_call` | `hermes.tool.finish` |
| `post_llm_call` | `hermes.turn.response` |
| `on_session_end` | `hermes.turn.finish` |
| `on_session_finalize` | `hermes.session.finish` |
| `on_session_reset` | `hermes.session.reset` |
| `subagent_start` | `hermes.subagent.start` |
| `subagent_stop` | `hermes.subagent.finish` |
| `pre_approval_request` | `hermes.approval.request` |
| `post_approval_response` | `hermes.approval.decision` |
| `pre_verify` | `hermes.verify.request` |
| `on_skill_lifecycle` | `hermes.skill.lifecycle` |
| Kanban hooks | `hermes.kanban.*` |

The adapter does not register `pre_tool_call`. Hermes treats that hook as a policy hook. A slow policy hook can block the tool.

The adapter queues events before it writes them. The queue prevents ledger I/O from blocking Hermes observer hooks. A full queue drops the new event and marks the recorder incomplete.

## Storage

The adapter writes one append-only JSONL shard for each process:

```text
<profile-plugin-data>/runs/<run-id-hash-or-unscoped>/<pid>-<process-start-id>.events.jsonl
```

Hermes supplies the active profile's plugin data directory. The adapter does not resolve storage from the process `HERMES_HOME` value.

The adapter creates all private storage directories with mode `0700`. It creates ledger files with mode `0600`. It opens storage components relative to pinned directory file descriptors. It rejects symbolic links, replaced path components, and unexpected hard links.

The recorder requires `O_NOFOLLOW`, `O_DIRECTORY`, directory-relative file operations, and no-follow status checks. It refuses initialization before any write when these controls are unavailable. Hermes isolates this plugin failure, so planning continues without recording.

The adapter sends no network traffic.

## Install and enable

Install the package in the Hermes runtime environment:

```bash
uv pip install --python "$HOME/.hermes/hermes-agent/venv/bin/python" .
hermes plugins enable observatory
```

Hermes discovers the `observatory` pip entry point on the next process start.

Use a managed run identifier when an external runner starts Hermes:

```bash
OBSERVATORY_RUN_ID="sprint-planning-2026-09-11-001" hermes chat -q "Run sprint planning."
```

An unmanaged desktop or CLI session uses its Hermes `session_id` as the event subject.

## Sprint-planning signals

The adapter observes the existing sprint-planning workflow. It does not modify that workflow.

Use these signals during analysis:

- `hermes.skill.lifecycle` with `workflow: "sprint-planning"` identifies exact sprint-planning skill use.
- `tool_family: "jira"` identifies Jira source access.
- `tool_family: "github"` identifies direct GitHub tool access.
- `tool_operation: "sprint_github_snapshot"` identifies the compact GitHub snapshot helper.
- `tool_operation: "sprint_plan_validation"` identifies canonical plan validation.
- `tool_operation: "qmd"` or `"gitnexus"` identifies knowledge-system use.
- `status`, `error_type_hash`, and `duration_ms` describe tool completion.
- `usage` contains numeric token accounting supplied by Hermes.

The adapter records helper classifications only. It does not record commands or paths.

## Recorder status

Run this slash command in a Hermes session:

```text
/observatory status
```

The result includes:

- probe readiness;
- ledger path;
- durable event count;
- queued event count;
- dropped event count;
- writer error count;
- a hash of the last writer error type;
- a `complete` flag.

`complete: false` means the shadow record lost an event or had a writer error. Ordinary Hermes work continues because this milestone uses observer-only hooks and fails open.

## Limits

Hermes plugin observers fail open. This adapter cannot prove that every expected hook fired. A later external runner must enforce readiness, required event pairs, run completion, and artifact hashes.

The adapter does not provide replay. Do not use Observatory `0.2.0` as a production replay system.
