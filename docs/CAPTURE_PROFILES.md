# Capture profiles

A capture profile applies the Observatory contract to one project or host.

The generic contract does not define one event vocabulary for all projects. A project derives a profile from the contract.

## Required profile decisions

A profile must define these items:

| Decision | Required content |
|---|---|
| Scope | The systems, sessions, and actions under observation. |
| Event kinds | The fixed event names and their meanings. |
| Join keys | The identifiers that connect related events. |
| Evidence grades | The grade for each capture surface. |
| Data policy | The allowed fields and excluded content. |
| Completeness | The expected events and incomplete-run rules. |
| Storage | The ledger type, path policy, and threat model. |
| Retention | The retention period and deletion authority. |
| Versioning | The profile name, version, and compatibility policy. |

## Data classes

A profile must classify each input before storage.

| Class | Example | Recommended treatment |
|---|---|---|
| Fixed enum | `completed` | Store only allowlisted values. |
| Numeric measure | duration or token count | Store bounded finite values. |
| Boolean | cache hit | Store as a Boolean. |
| Opaque identifier | session ID | Use a keyed hash over the complete value. Separate each field domain. |
| Content | prompt, response, command, path | Exclude or redact before storage. |
| Credential | token or password | Never store the value. |

Do not use the word `metadata` as a safety claim. Arbitrary strings can contain content or credentials.

## Completeness states

A profile must distinguish availability from completeness.

- `ready`: The recorder passed its startup probe.
- `complete`: The recorder captured all expected events known to the profile.
- `incomplete`: The recorder detected a drop, write error, missing event, or unsupported join.
- `unavailable`: The recorder could not start safely.

A fail-open adapter can keep the observed system running. It must mark the evidence incomplete or unavailable.

## Storage threat model

Each profile must state what it protects against.

`JsonlLedger` assumes a trusted local path. It does not defend against link replacement or hostile parent directories.

`SecureJsonlLedger` protects private local records on supported POSIX systems. It uses descriptor-relative and no-follow file operations.

It uses `0700` directories and `0600` files. It rejects parent traversal, links, and inode replacement. It uses an interprocess file lock.

It refuses to start when these controls are unavailable.

A profile can use another ledger. It must document equivalent behavior and run suitable conformance tests.

## Versioning

Give each profile a stable name and version. For example:

```text
example-agent.metadata.v1
```

Add fields in a compatible release. Change the profile version when field meaning, event meaning, or correlation behavior changes.
