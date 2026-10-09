# EC2 Watchman

Watchman is an AWS Lambda that scans enabled EC2 regions every hour and stops running instances older than 12 hours. A keep-{days} tag extends an instance's lifetime. It records every stop attempt in DynamoDB and posts one Slack shutdown report each Friday. Sweep failures alert in Slack immediately.

## Behavior

- The hourly sweep discovers regions, checks every page of instances, and calls StopInstances for eligible instances.
- A successful stop request is tagged with watchman-stopped-at and recorded with its name, ID, region, age, and UTC time.
- Failed stop requests are also recorded, and the sweep sends one aggregated failure alert. Region discovery, tagging, or DynamoDB write failures appear in the alert and CloudWatch Logs.
- The Friday report lists stopped and failed attempts by region. It posts an explicit “No instances stopped” message when the period is empty.
- The report covers the interval since the last successful report. The first report covers the prior seven days, but can only include events recorded after the DynamoDB table was created. If a report fails, the next one catches up. The watermark advances only after Slack accepts the message.
- Slack messages limit listed regions and instances to stay within Block Kit limits. The totals still include all recorded events.

Watchman **stops** instances. It does not terminate them or delete CloudFormation stacks.

## Keep tags

Add a tag whose **key** uses the keep-{days} format. For example, keep-7 keeps an instance running for seven days after launch. The tag value is ignored. Without a keep tag, the threshold is 12 hours.

## Architecture and configuration

The SAM template creates one Lambda, two EventBridge schedules, a DynamoDB shutdown-events table, and a CloudWatch log group. The table uses on-demand billing, server-side encryption, and a 35-day TTL for event items. A separate watermark item has no TTL. The table has DeletionPolicy: Retain, so removing the stack leaves its data for manual cleanup.

| Parameter                | Default                | Purpose                                              |
| ------------------------ | ---------------------- | ---------------------------------------------------- |
| ScheduleExpression       | rate(1 hour)           | Hourly sweep                                         |
| ReportScheduleExpression | `cron(0 10 ? * FRI *)` | Friday report at 16:00 UTC                           |
| ShutdownRetentionDays    | 35                     | Event retention; at least 7 days                     |
| SlackWebhookURL          | empty                  | Slack Incoming Webhook; empty disables notifications |
| LambdaMemorySize         | 256                    | Memory in MB                                         |
| LambdaTimeout            | 300                    | Timeout in seconds                                   |

The Friday schedule runs at noon Eastern during daylight saving time and 11:00 a.m. Eastern during standard time. Both schedules use UTC. The report goes to the same webhook as failure alerts.

## Deploy

Prerequisites: AWS credentials with stack deployment permissions, AWS SAM CLI, and Python 3.11.

From this directory:

```bash
sam build
sam deploy --guided
```

For an existing stack, retain its current parameter values when adding the report schedule and table. To set the webhook:

```bash
sam deploy --parameter-overrides SlackWebhookURL="https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
```

The SlackWebhookURL parameter has NoEcho: true, but the Lambda environment variable still contains the webhook. Limit access to the Lambda configuration and local samconfig.toml. Do not commit the webhook.

If no webhook is configured, hourly shutdowns continue and the report watermark does not advance. Configure a webhook before expecting a weekly report.

## IAM and data

The Lambda role can describe regions and instances, stop instances, and write tags across EC2 regions. It also has PutItem, Query, GetItem, and UpdateItem on **only** its shutdown-events table. The event log stores instance ID, name, region, age, stop time, outcome, and a short error for failed stop requests. It does not store webhook credentials.

DynamoDB partitions events by UTC month. Reports use bounded, paginated queries across every month in the unreported window. A strongly consistent read is used before posting. The meta / last_report item stores the last successfully posted report boundary.

## Test and operate

Run offline unit tests:

```bash
python3 -m unittest discover -s tests -v
```

The test suite stubs AWS and Slack; it does not use account credentials or send messages. A local SAM invocation with the default event runs the sweep, so use it only with intended AWS credentials.

Check /aws/lambda/ec2-watchman for sweep and report logs. A normal sweep returns shutdown and failure counts. A failure in one region does not prevent other regions from being processed. A failed report leaves the watermark in place for the next Friday run. If DynamoDB fails during a stop, the stop still proceeds and an immediate failure alert is attempted; that event cannot appear in the later digest unless the data is repaired.

The first Friday after deployment may report fewer than seven days of events. The report's UTC window shows exactly what it covers.
