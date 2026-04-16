# Edge Scrum Law: Jira Custom Fields

> The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in [RFC 2119](https://datatracker.ietf.org/doc/html/rfc2119).

## Custom Fields (Red Hat Jira Instance)

| Field | Custom Field ID | Type | Usage |
|---|---|---|---|
| Story Points | `customfield_10028` | Numeric | SP value for Stories, Tasks, Spikes |
| Epic Link | `customfield_10014` | Issue link | Story → Epic relationship |
| Parent Link | `customfield_10018` | Issue link | Epic → Feature/Initiative relationship |
| QA Contact | `customfield_10470` | User picker | QA owner for an issue |
| Flagged | `customfield_10021` | Array | Non-empty = impediment flag |
| Doc Contact | `customfield_10473` | User picker | Documentation owner for an issue |
| SME | `customfield_10475` | User picker | Subject Matter Expert for a Feature/Initiative |
| T-shirt Size | `customfield_10795` | String | Size (XS/S/M/L/XL) for Features and Initiatives |

Agents MUST use these exact field IDs when constructing Jira API calls. Never hardcode field names as strings in JQL — use the `customfield_` ID.
