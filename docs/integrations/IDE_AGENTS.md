# Observe agents in VS Code and Cursor

This page describes a generic integration design. Observatory does not require an IDE or agent runtime.

## Design rule

Observe the agent at the most direct event source that it provides. Use the IDE only for host events that the agent cannot provide.

The IDE does not make all agents equivalent. An agent in a terminal, a native Cursor agent, and a Claude Code extension expose different evidence.

## Coverage matrix

| Agent surface | Preferred source | Available evidence | Main limit |
|---|---|---|---|
| Cursor Agent and Cmd+K | Cursor command hooks | Sessions, generations, selected models, tools, shell calls, MCP calls, edits, subagents, compaction, and completion | Hook payloads contain content fields. The adapter must reduce them before storage. |
| Cursor Tab | Cursor Tab hooks | File-read and file-edit lifecycle | This surface does not describe a complete agent session. |
| Cursor cloud agent | Project or managed Cursor hooks | Tool, shell, edit, subagent, compaction, and completion events | Some IDE session hooks do not run in cloud agents. |
| Cursor Enterprise | Cursor OpenTelemetry export | Model usage, tool totals, cost estimates, requests, errors, hooks, skills, plugins, and cloud lifecycle | It is server-side and requires Enterprise. Metrics do not have conversation IDs. |
| Claude Code in a terminal | Claude Code command hooks | Session, turn, tool, permission, subagent, task, compaction, and completion events | Some hooks can control execution. Passive capture must not use control output. |
| Claude Code extension in VS Code or Cursor | Claude Code hooks | The same Claude Code runtime events | The IDE host does not add model evidence unless its extension exposes it. |
| Another agent in an IDE terminal | Native agent hooks, then a process wrapper | Process start, finish, exit, duration, and adapter-specific events | A shared terminal cannot prove whether the user or agent ran a command. |
| VS Code task | Task wrapper and VS Code task events | Task start, process start, process end, and exit status | Tasks require a workspace folder and explicit configuration. |

Cursor documents separate hooks for Agent, Tab, and application lifecycle events. Its common hook input includes conversation, generation, and model identifiers.[1]

Claude Code documents hooks for session, prompt, tool, permission, subagent, task, and completion lifecycle events.[3]

## Use one adapter per native surface

Do not build one IDE extension that reads every vendor transcript. Build small adapters:

```text
Cursor hooks ────────────┐
Claude Code hooks ───────┤
Optional adapter for Hermes users ─┤
Terminal wrapper ────────┼─> profile events ─> private ledgers ─> merger
VS Code host extension ──┤
Cursor OTLP export ──────┘
```

Each adapter owns its source schema and metadata policy. The merger owns correlation and deduplication.

## Cursor recommendation

### Local and cloud agents

Use command-based Cursor hooks. Cursor reads project hooks from `.cursor/hooks.json`. It can also read user, team, and enterprise hook sources.[1]

Use passive completion hooks when possible:

- `sessionStart` and `sessionEnd`;
- `postToolUse` and `postToolUseFailure`;
- `afterShellExecution`;
- `afterMCPExecution`;
- `afterFileEdit`;
- `subagentStart` and `subagentStop`;
- `preCompact`;
- `stop`.

Do not store hook input directly. Shell hooks can include full commands and output. Tool hooks can include tool input and results. Agent response hooks can include generated text.[1]

Do not return control fields from a passive recorder. Cursor command hooks can block or modify behavior. Nonzero failures usually fail open, but a configured fail-closed hook can block execution.[1]

Project command hooks also run in Cursor cloud agents. Some session, MCP, Tab, and workspace hooks are unavailable in that environment.[1]

### Teams that use Claude Code hooks in Cursor

Cursor can load selected Claude Code hooks and map them to Cursor events. This requires the third-party configuration option. The compatibility layer does not support every Claude Code hook or tool name.[6]

Use separate source schema values for native Cursor hooks and compatibility hooks. This prevents an adapter from claiming identical coverage.

### Enterprise usage export

Cursor Enterprise can send metrics and logs to a team-managed OpenTelemetry collector. The export includes token metrics, tool-call metrics, cost estimates, request logs, errors, and cloud-agent lifecycle logs.[2]

Treat this export as an organization-level source. Logs use at-least-once delivery. Metrics use at-most-once delivery. Deduplicate logs with `cursor.event.id`.[2]

Do not use metric totals as session totals. Cursor states that metric points do not contain conversation IDs. Use request logs for conversation-level model usage.[2]

The export does not include prompt content or OpenTelemetry traces. It also has no historical backfill.[2]

### Cursor subscriptions, models, and billing

Cursor plan type changes the available usage pools and organization data. Individual plans can use local hooks. The OpenTelemetry export requires Enterprise.[1][2]

Pro, Pro Plus, and Ultra have separate Cursor Models and Other Models pools. Third-party models use the Other Models pool at the listed API rate.[7]

Teams uses per-user pools. Enterprise can use pooled team usage. Teams also provides administration and usage data.[8]

On Teams and Enterprise, Cursor Router selects a model for Auto requests. The selected route can change with the optimization mode.[7]

Record model and billing evidence as separate values:

- Hash the requested model or Auto mode when a native event provides it.
- Hash the resolved model when a native event provides it.
- Store token counts only from the source that reports them.
- Store an estimated cost as an estimate. Do not label it as billed cost.
- Store the plan and usage-pool class only as fixed allowlisted values.

Cursor Enterprise OpenTelemetry includes estimated cost metrics. Treat these metrics as estimates, not invoice records.[2]

The Cloud Agents API reports token usage for each run and agent. Combine those values with a dated price table only when you need an estimate.[9]

Use the Cursor usage dashboard or an authorized billing export for billed usage. Do not infer an invoice from hooks, token counts, or model names.

This separation supports these cases:

| Cursor use | Local evidence | Organization evidence | Billing limit |
|---|---|---|---|
| Individual subscription | Local Cursor hooks | User usage dashboard | Hooks do not prove billed cost. |
| Teams | Local hooks | Team usage and administration data | Usage belongs to each user pool. |
| Enterprise | Local hooks and OTLP export | Pooled usage and organization export | OTLP cost values remain estimates. |
| Cloud Agents API | Project hooks and API run records | Per-run token usage | Apply prices separately and date the estimate. |

## Claude Code recommendation

Use command hooks in the Claude Code runtime. This applies whether Claude Code runs in a normal terminal or an IDE extension.[3]

Prefer these passive events:

- `SessionStart` and `SessionEnd`;
- `PostToolUse` and `PostToolUseFailure`;
- `PostToolBatch`;
- `SubagentStart` and `SubagentStop`;
- `TaskCreated` and `TaskCompleted`;
- `PreCompact`;
- `Stop` and `StopFailure`.

Do not use `PreToolUse` for passive recording. It can block or change a tool call.[3]

Do not persist stdin payloads. Reduce them to fixed event names, hashes, counts, durations, and allowlisted status values.

## VS Code host recommendation

A small VS Code extension can observe the host around any terminal agent. The VS Code API provides terminal shell-execution events. These events fire only when terminal shell integration is active.[4]

The extension can also observe task and process lifecycle events. VS Code tasks can start external processes from workspace configuration.[5]

Use the extension for these generic events:

- IDE workspace open and close;
- dedicated observed-terminal creation and close;
- shell command start and end;
- task start and end;
- process exit status;
- test-run start and end;
- source-control state counts.

Do not store command text, terminal output, file paths, document contents, diffs, or diagnostic messages.

A host extension has limited attribution. It can prove that a command ran in a terminal. It cannot prove which model selected that command. Use a native agent event for that claim.

## Direct terminal agents

Launch an agent through a wrapper when it has no native hook interface.

The wrapper can record:

- adapter version;
- executable family from a fixed allowlist;
- process-start identifier hash;
- start and finish times;
- exit class and code;
- duration;
- child-process count;
- bytes written to stdout and stderr;
- whether the run was interrupted.

Do not record argv values or environment values. Both can contain prompts and credentials.

Use a dedicated terminal for an observed agent. Do not infer agent activity from all commands in a shared terminal.

## Correlation

Create one opaque run identifier at the planning boundary. Pass it to adapters through a supported session field or environment variable.

Store only a domain-separated hash of the run identifier. Also store native conversation and generation identifiers as hashes.

Use these keys:

| Level | Key |
|---|---|
| Planned work | `run_id_hash` |
| Agent session | `session_id_hash` |
| Turn or generation | `turn_id_hash` |
| Model request | `request_id_hash` |
| Tool action | `tool_call_id_hash` |
| Host process | `process_start_id_hash` plus PID |

Do not force a join when the source does not provide one. Mark the relation unavailable.

## Common event profile

Adapters can map native records to these generic families:

```text
workspace.start
workspace.finish
agent.session.start
agent.session.finish
agent.turn.start
agent.turn.finish
model.request.finish
model.request.error
tool.finish
tool.error
subagent.start
subagent.finish
file.edit
terminal.command.finish
task.finish
context.compact
approval.decision
```

A project can use different event names. It must define them in its capture profile.

## Evidence and completeness

Assign the grade from the source:

- Native vendor hook or vendor export: `native`.
- Adapter-owned process wrapper: `seam`.
- Local API or protocol interception: `proxy`.
- Agent summary: `self_reported`.

Do not combine partial sources into a false complete trace.

Examples:

- A Cursor hook can prove a Cursor tool event.
- A VS Code terminal event can prove a shell command lifecycle.
- A file watcher can prove a file changed.
- None of these alone proves which model caused the change.

Set `complete: false` when an adapter drops an event, misses a required join, starts late, or loses storage access.

## Recommended implementation order

1. Add native hook adapters for the agent runtimes in current use.
2. Add a terminal wrapper for agents without hooks.
3. Add a VS Code extension for dedicated terminal and task lifecycle events.
4. Add Cursor OpenTelemetry ingestion if the team has Enterprise.
5. Add a merger that deduplicates events and reports coverage by source.
6. Add dashboards only after completeness checks pass.

This order gives direct evidence before broad IDE inference.

## Sources

[1]: https://cursor.com/docs/hooks
[2]: https://cursor.com/docs/enterprise/opentelemetry-export
[3]: https://docs.anthropic.com/en/docs/claude-code/hooks
[4]: https://code.visualstudio.com/api/references/vscode-api
[5]: https://code.visualstudio.com/docs/editor/tasks
[6]: https://cursor.com/docs/reference/third-party-hooks
[7]: https://cursor.com/docs/account/pricing
[8]: https://cursor.com/docs/account/teams/pricing
[9]: https://cursor.com/docs/cloud-agent/api/endpoints
