# Edge Scrum Law: Team Roster

> The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in [RFC 2119](https://datatracker.ietf.org/doc/html/rfc2119).

An issue belongs to the team if a roster member is the **assignee** or the **QA Contact** (`customfield_10470`). Exclude issues where neither field matches a roster member.

The roster is defined in `.roster.json` in the plugin directory (`plugins/edge-scrum/.roster.json`). This file is excluded from version control — copy `.roster.json.example` to `.roster.json` and populate it with your team. Agents and skills MUST read this file at runtime to determine team membership, per-member SP targets, and total capacity.
