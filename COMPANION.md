# Companion: Agent Observability Guidebook

Observatory has a companion repository,
[`agent-observability-guidebook`](https://github.com/brennenawana/agent-observability-guidebook).
The guidebook is currently private. If the link returns 404, you do not have access to it.
This repository does not depend on it.

## Relationship

| | `observatory` (this repository) | `agent-observability-guidebook` |
|---|---|---|
| Role | The contract: how to record trustworthy evidence | The application: what to record about coding agents and how to use it to reduce spend |
| Contents | Events, append-only ledger, evidence grades, capture and replay, write-time redaction, probes, capture profiles, completeness, storage threat models, and a reference Python package | Hermes and Claude Code capture patterns, workflow events, cost accounting, session close-out reports, normalized facts, optimization detectors, dashboard rules, and pitfalls |
| Form | Specification plus reference code | Prose only. It contains no code to copy |
| Neutrality | No runtime or IDE dependency | Opinionated. It describes one working Hermes and Claude Code setup |
| Change model | Versioned package releases | Static. It changes only on explicit request and records pattern changes in its changelog |

The two repositories are separate on purpose. The contract must stay neutral and reusable. The guidebook makes runtime-specific and goal-specific choices that the contract must not impose.

If the two repositories disagree about the contract, this repository is authoritative. The guidebook builds on this contract. It does not redefine it.

## Agent reading path

Use this sequence when an agent must design or implement agent observability. It takes about 15 minutes.

1. This repository:
   1. `README.md`: the six operations and the two ledger types.
   2. `CONTRACT.md` §1–3 (event, ledger, grade), §5 (redaction), §7 (capture profiles), and §8 (completeness).
   3. `docs/CAPTURE_PROFILES.md`: the decisions a profile must make.
   4. Optional: `docs/integrations/` for the target runtime (Hermes, Claude Code, Cursor, or VS Code).
2. The guidebook:
   1. `README.md`, then `docs/01-principles.md` and `docs/02-architecture.md`.
   2. The chapter for the task:

| Task | Guidebook chapter |
|---|---|
| Record every session, turn, model call, and tool call | `03-passive-runtime-capture.md` and `appendix-hermes-hooks.md` |
| Record workflow and skill runs with outcomes | `04-workflow-events.md` |
| Attach cost to sessions, tickets, or workflows | `05-cost-accounting.md` |
| Produce a report for each session or orchestration tree | `06-session-close-out.md` |
| Compare sessions over time | `07-normalized-facts.md` |
| Find recurring waste | `08-optimization-findings.md` |
| Build a local viewer | `09-operator-dashboard.md` |

3. Before implementation, read the guidebook's `docs/10-pitfalls.md`.

## Example prompts

Run each session from the repository that the work changes. Give the agent the other repositories as read-only references by local path or URL.

### From the project that you want to instrument

This is the common case. The target project owns its capture profile, hooks, and reports.

```text
Add passive observability to this project's agent workflows.
Read ~/code/observatory (README, CONTRACT.md §1-3, 5, 7, 8, docs/CAPTURE_PROFILES.md)
and ~/code/agent-observability-guidebook (README, docs/01, 02, 04, 10) first.
Write a capture profile document before you change code. Then hook each workflow's
unavoidable artifact, not a step in the skill text. Use observatory as the ledger dependency.
Treat both repositories as read-only references.
```

```text
Build a cost report for this project's agent sessions for the last 7 days.
Follow ~/code/agent-observability-guidebook docs/05 and 06 for the token arithmetic and layer split.
Label every figure as API-equivalent. Do not guess prices for unknown models.
Report coverage gaps before totals.
```

### From this repository (`observatory`)

Use this case only when the contract or the reference package must change.

```text
A consumer needs <capability> that the contract does not cover.
Read CONTRACT.md and docs/CAPTURE_PROFILES.md. Read ~/code/agent-observability-guidebook
docs/01 and 10 for the consumer's context.
Propose the smallest runtime-neutral contract change. If the need is specific to Hermes or
cost analysis, explain why it belongs in an adapter or project profile instead.
```

### From an adapter repository (for example, a Hermes plugin)

```text
Add the <hook> Hermes hook to the passive recorder.
Read ~/code/observatory CONTRACT.md §5, 7, and 8, and ~/code/agent-observability-guidebook
docs/03 and appendix-hermes-hooks.md. Verify the hook's real payload and emission order
in the Hermes source before writing the mapping. Store only metadata, as chapter 03 describes.
```

### From the guidebook repository

Use this case only when the owner explicitly requests a guidebook update.

```text
Update the guidebook for the pattern changes in <source repository> since <date or commit>.
Summarize patterns, decisions, and pitfalls. Do not copy code or scripts.
Remove organization-specific names. Add one CHANGELOG entry that states what changed and why.
```
