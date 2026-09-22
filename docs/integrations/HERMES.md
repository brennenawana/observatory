# If you use Hermes

This page recommends one Hermes integration. Hermes is not required by Observatory.

Keep the runtime adapter in a separate package. Do not add a Hermes entry point to the generic `observatory` distribution.

A suitable package boundary is:

```text
hermes-observatory/
├── src/hermes_observatory/
│   ├── plugin.py
│   ├── profile.py
│   ├── metadata_policy.py
│   └── recorder.py
└── tests/
```

## Recommended capture surface

Use passive Hermes plugin hooks. Hermes supports plugin hooks in the CLI and gateway.[1]

Do not register a model-visible Observatory tool. Do not add Observatory instructions to the model prompt.

Do not use control hooks for passive recording. The `pre_tool_call` and `pre_verify` hooks can affect execution. Prefer completion and lifecycle hooks.

## Recommended event profile

Define a versioned Hermes profile in the adapter package. Map native events for these areas when your Hermes version provides them:

- sessions and turns;
- model requests and errors;
- completed tool calls;
- subagents;
- approvals;
- skill lifecycle;
- coordinated worker lifecycle.

Use keyed hashes for complete opaque identifiers. Define the key scope and rotation policy. Store fixed enums, bounded counts, durations, and usage values.

Exclude prompts, responses, commands, paths, tool arguments, tool results, and source content.

## Recommended storage

Use the profile-scoped plugin data directory supplied by Hermes. Do not derive the path from another profile.

Use `SecureJsonlLedger` when the required file operations are available. If secure storage is unavailable, disable recording and report the reason. Do not stop ordinary Hermes work.

Use a bounded background queue. A queue drop or writer error must mark the record incomplete.

Define required event pairs and identifier joins. Do not report complete before a full session lifecycle is durable.

## Recommended verification

Before you enable the adapter for normal work, verify these conditions:

1. Hermes loads the adapter through its public plugin API.
2. The adapter registers no model-visible tool.
3. The adapter does not register `pre_tool_call` or `pre_verify`.
4. A planted prompt and response do not appear in ledger bytes.
5. Storage uses private permissions.
6. Unload stops the writer.
7. Queue drops and write failures appear in status.

This setup is one implementation recommendation. Another Hermes adapter can conform to the generic Observatory contract with a different profile.

## Sources

[1]: https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks
