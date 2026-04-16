# Edge Scrum Law: Stories, Spikes, and Tasks

> The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in [RFC 2119](https://datatracker.ietf.org/doc/html/rfc2119).

## Issue Hierarchy

```text
Stories / Spikes / Bugs / Tasks
  └── Epics  (linked via Epic Link field)
        └── Features / Initiatives  (linked via Parent Link field)
```

- Stories/Spikes/Bugs SHOULD always link to an Epic via **Epic Link**.
- Epics link to a Feature or Initiative via **Parent Link** (RECOMMENDED, not REQUIRED).
- Stories without an epic are non-compliant.

**Types NOT used by this team:** Ticket, Subtask (enabled but excluded from scrum functions).

## Issue Types

| Type | Purpose | Sizing | PR Required |
|---|---|---|---|
| **Story** | Capability delivery from the user's perspective | Story Points (fibonacci) | Yes |
| **Spike** | Time-boxed research; ends with written stories or a new spike | Story Points (fibonacci) | No |
| **Task** | Finite piece of work; post-meeting follow-ups, action items | Story Points (fibonacci) | No |

## Story Pointing

Story points use the **fibonacci sequence**: 0, 1, 2, 3, 5, 8, 13.

Points represent the fraction of a sprint's capacity consumed by the work item:

| Points | Meaning |
|---|---|
| 0 | Trivial; entered for transparency (or any bug) |
| 1 | < 25% of sprint capacity |
| 2 | 25–40% of sprint capacity |
| 3 | 40–60% of sprint capacity |
| 5 | 60–90% of sprint capacity |
| 8 | 90%+ of sprint capacity |
| 13 | Full sprint of dedicated focus — likely too big; split or create an Epic |

**Rules:**

- MUST be pointed by the **assignee**.
- Target: **8 SP per team member per sprint** (acceptable range: 8–10).
- MUST repoint on assignee change if the new assignee's capacity differs.
- If a story's scope changes mid-sprint, repoint and leave a comment explaining why.
