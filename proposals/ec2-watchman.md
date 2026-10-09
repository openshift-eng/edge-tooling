# Proposal: EC2 Watchman

**Status:** Accepted (operational; implementation lives in [`watchman/`](../watchman/))  
**Author:** eggfoobar
**Date:** 2026-04-16

## Summary

**EC2 Watchman** is a scheduled AWS Lambda that scans **all EC2 regions** in an account, identifies running instances that exceed a configured age policy (with optional `keep-{days}` tags to extend lifetime), **stops** those instances, and records each stop attempt in DynamoDB. It posts a weekly shutdown report and immediate failure alerts to an optional **Slack incoming webhook** (for Red Hat Slack, webhooks are commonly created via the [Eddie](slack-bot-eddie.md) app). It is a cost-control and hygiene tool for shared AWS accounts where long-lived dev or test instances would otherwise run indefinitely.

Code and deployment assets: [`watchman/README.md`](../watchman/README.md), SAM template [`watchman/template.yaml`](../watchman/template.yaml).

## Goals and non-goals

- **Goals:** Automatically stop stale EC2 instances across regions; make exceptions explicit via tags; provide a weekly audit trail and immediate failure visibility via Slack.
- **Non-goals:** Terminate instances or tear down CloudFormation stacks; guarantee RTO for workloads on those instances; enforce tagging policy beyond `keep-*` semantics.

## Architecture

Two Amazon EventBridge schedules invoke the same Lambda in the **stack region**: an hourly sweep and a Friday 16:00 UTC report. The sweep calls EC2 APIs globally (`DescribeRegions`, then per-region `DescribeInstances` / `StopInstances` / `CreateTags`) and writes stop outcomes to DynamoDB. The report queries events since its last successful Slack post and advances a watermark only after the post succeeds. Optional HTTPS calls to Slack use the webhook URL from Lambda environment variables.

```mermaid
flowchart TB
  subgraph aws [AWS account]
    EBH[EventBridge hourly sweep]
    EBW[EventBridge Friday report]
    L[Lambda ec2-watchman]
    DDB[(DynamoDB shutdown events and watermark)]
    CW[CloudWatch Logs]
    EBH --> L
    EBW --> L
    L --> CW
    L --> DDB
    subgraph regions [All enabled regions]
      EC2[EC2 Describe / Stop / Tag]
    end
    L --> EC2
  end
  SL[Slack Incoming Webhook]
  L -- weekly report and failure alerts --> SL
```

**Trust / data:** The function’s IAM role is broad (`Resource: '*'` on EC2 actions listed in the template) because it must operate in every region. DynamoDB permissions are scoped to the shutdown-events table. Records hold instance id, name, region, age, stop time, and outcome for 35 days by default; a report watermark persists without TTL. Slack receives these fields in the weekly report and operational failure details immediately. Treat the webhook URL as a secret (see [Eddie](slack-bot-eddie.md) for the Slack-side app that issues such webhooks).

## Impact if unavailable

- **Cost / hygiene:** Instances that would have been stopped continue to run; **AWS spend and quota use can increase** until someone notices and cleans up manually.
- **Policy drift:** Teams relying on Watchman for “default off” behavior lose that guardrail; nothing automatically enforces shutdown.
- **No direct user-facing outage:** Watchman does not serve traffic; workloads on EC2 keep running (which is exactly why failure increases cost risk rather than application downtime).

Slack-only failure: shutdowns still occur; operators lose the report and immediate alerts. A failed report leaves its watermark unchanged so the next scheduled report can catch up. DynamoDB write failure does not prevent stopping, but that event cannot appear in the report and triggers an immediate alert attempt.

## Recovery when it goes down

1. **Detect:** CloudWatch **Errors** / **Throttles** on the function, missing **invocations** on either schedule, or absence of expected log lines under `/aws/lambda/ec2-watchman` (see [`watchman/README.md`](../watchman/README.md#test-and-operate)).
2. **Diagnose:** Read recent log streams; check per-region log lines (`Error processing region …`). Common issues: IAM changes, disabled regions, timeout under very large inventories.
3. **Restore service:** Redeploy or update the CloudFormation / SAM stack from this repo (`sam build` / `sam deploy` per [`watchman/README.md`](../watchman/README.md#deploy)); confirm both schedules are enabled, the DynamoDB table is accessible, and the Slack webhook is configured. Verify the report watermark before a manual report invocation.
4. **Mitigate cost while broken:** Manual stops, AWS Budgets/alerts, or temporary schedules—Watchman does not replace those for critical accounts.

## Cost to team or organization

- **AWS:** Lambda invocations, duration (default 256 MB, 300 s max), **CloudWatch Logs** (14-day retention), and a DynamoDB on-demand table with 35-day event TTL. At expected volume the table cost is small relative to EC2 left running.
- **Slack:** Incoming Webhooks are usually covered under existing Slack workspace terms; no separate product fee for the webhook mechanism itself.
- **Savings:** Successful runs **reduce** EC2 usage charges by stopping instances that meet the policy.

## Maintenance cost for the team

- **Runtime / dependencies:** Python 3.11 Lambda; [`watchman/requirements.txt`](../watchman/requirements.txt) includes `requests` (packaged with deployment). Occasional **runtime deprecation** requires SAM/template updates.
- **Policy alignment:** The enforced age threshold and tag rules live in [`watchman/lambda_function.py`](../watchman/lambda_function.py); changes need code review and redeploy. The default threshold is **12 hours** without a `keep-*` tag.
- **State and retention:** The table is retained if the stack is deleted, so operators must remove it manually when appropriate. The report can catch up after missed Fridays only while its events remain within retention.
- **Secrets / config:** Rotate or replace Slack webhook if leaked; update stack parameters.
- **On-call:** Low if schedules and alarms exist; spikes if accounts grow very large and timeouts need tuning (see README limitations).

## Alternatives considered

- **Manual or scripted cleanup:** Flexible but inconsistent and easy to skip under time pressure.
- **AWS Instance Scheduler / Systems Manager:** First-party scheduling with different setup and cost model; may fit some orgs better for fixed windows.
- **Budgets + anomaly detection only:** Alerts without automated stop—less enforcement, lower blast radius if misconfigured.

Watchman trades **automation and coverage (all regions)** for **strong EC2 stop permissions** and the need to tag exceptions explicitly.

## Decision

**Accepted** as the repo’s standard pattern for automated EC2 lifecycle trimming in accounts where this stack is deployed. Revisit if AWS introduces conflicting org-wide policies or if stop-only semantics are insufficient (e.g. mandatory terminate + stack cleanup).
