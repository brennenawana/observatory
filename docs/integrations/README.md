# Optional integrations

Observatory remains independent of every agent runtime and IDE.

An integration adapter translates one host event surface into a project capture profile. Keep the adapter outside the core `observatory` package.

## Adapter boundary

An adapter usually has four parts:

1. A host connector receives native events.
2. A metadata policy reduces each payload before storage.
3. A profile maps host events to versioned event kinds.
4. A recorder writes events and reports completeness.

The host connector can use hooks, an extension API, a task wrapper, or a telemetry export.

## Evidence order

Prefer observation surfaces in this order:

1. Native lifecycle hooks or vendor telemetry.
2. An in-process seam owned by the integration.
3. A process or protocol boundary.
4. A self-reported agent summary.

Do not report process evidence as native model evidence. Do not report filesystem changes as proof of which agent produced them.

## Multiple adapters

Use one logical run identifier across adapters. Store a reduced form when the identifier can contain project data.

Each adapter must also write these fields:

- adapter name and version;
- profile name and version;
- source event version;
- process or producer identifier;
- sequence number when available;
- evidence grade;
- completeness status.

A merger must deduplicate native events before it calculates totals.

## Recommendations

- [IDE and terminal agents](IDE_AGENTS.md)
- If you use Hermes, see the [optional setup recommendation](HERMES.md).

These pages explain optional setups. Observatory does not require them.
