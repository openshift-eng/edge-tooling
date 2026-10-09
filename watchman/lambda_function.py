"""EC2 Watchman: hourly shutdown sweep and weekly Slack digest."""

import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional

import boto3
import requests
from boto3.dynamodb.conditions import Key


def parse_keep_days(tags: List[Dict]) -> Optional[int]:
    for tag in tags or []:
        key = tag.get("Key", "")
        if key.startswith("keep-"):
            match = re.search(r"keep-(\d+)", key, re.IGNORECASE)
            if match:
                return int(match.group(1))
    return None


def get_instance_age_hours(instance: Dict) -> float:
    launch_time = instance["LaunchTime"]
    if launch_time.tzinfo is None:
        launch_time = launch_time.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - launch_time).total_seconds() / 3600


def should_shutdown_instance(instance: Dict) -> bool:
    if instance.get("State", {}).get("Name") in ("stopped", "stopping", "terminated", "terminating"):
        return False
    age_hours = get_instance_age_hours(instance)
    keep_days = parse_keep_days(instance.get("Tags", []))
    return age_hours >= (keep_days * 24 if keep_days else 12)


def get_all_regions() -> List[str]:
    response = boto3.client("ec2", region_name="us-east-1").describe_regions()
    return [region["RegionName"] for region in response["Regions"]]


def get_instance_name(instance: Dict) -> str:
    for tag in instance.get("Tags", []):
        if tag.get("Key") == "Name":
            return tag.get("Value", "")
    return instance.get("InstanceId", "unknown")


def utc_iso(value: datetime) -> str:
    """Fixed-width UTC strings sort chronologically as DynamoDB sort keys."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Report watermark must include a timezone")
    return parsed.astimezone(timezone.utc)


def shutdown_table():
    return boto3.resource("dynamodb").Table(os.environ["SHUTDOWN_TABLE_NAME"])


def record_shutdown_event(instance_id: str, instance_name: str, region: str,
                          age_hours: float, stopped_at: datetime, status: str,
                          error: Optional[str] = None) -> None:
    timestamp = utc_iso(stopped_at)
    item = {
        "pk": f"shutdown#{timestamp[:7]}",
        "sk": f"{timestamp}#{instance_id}",
        "instance_id": instance_id,
        "instance_name": instance_name if instance_name != instance_id else "",
        "region": region,
        "age_hours": Decimal(str(round(age_hours, 2))),
        "stopped_at": timestamp,
        "stop_status": status,
        "expires_at": int(stopped_at.timestamp()) + int(os.environ["SHUTDOWN_RETENTION_DAYS"]) * 86400,
    }
    if error:
        item["error"] = error[:500]
    shutdown_table().put_item(Item=item)


def read_watermark() -> Optional[datetime]:
    response = shutdown_table().get_item(
        Key={"pk": "meta", "sk": "last_report"}, ConsistentRead=True
    )
    value = response.get("Item", {}).get("reported_through")
    return parse_utc(value) if value else None


def write_watermark(through: datetime) -> None:
    shutdown_table().update_item(
        Key={"pk": "meta", "sk": "last_report"},
        UpdateExpression="SET reported_through = :through",
        ExpressionAttributeValues={":through": utc_iso(through)},
    )


def query_events(start: datetime, end: datetime) -> List[Dict]:
    """Read [start, end) across every month, with pagination and strong consistency."""
    events = []
    table = shutdown_table()
    month = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while month < end:
        next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        lower = max(start, month)
        upper = min(end, next_month)
        # An event at upper has a #instance-id suffix, so it sorts after the
        # plain upper timestamp and is excluded by BETWEEN's inclusive bound.
        condition = (Key("pk").eq(f"shutdown#{month:%Y-%m}")
                     & Key("sk").between(utc_iso(lower), utc_iso(upper)))
        kwargs = {"KeyConditionExpression": condition, "ConsistentRead": True}
        while True:
            page = table.query(**kwargs)
            events.extend(page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        month = next_month
    return events


def escape_slack(value: object) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_digest_blocks(events: List[Dict], start: datetime, end: datetime) -> List[Dict]:
    stopped = sum(event.get("stop_status") == "ok" for event in events)
    failed = len(events) - stopped
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "EC2 Watchman weekly shutdown report"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text":
            f"{utc_iso(start)} to {utc_iso(end)} · {stopped} stopped · {failed} failed"}]},
    ]
    if not events:
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": "No instances stopped in this period."}})
        return blocks

    grouped = defaultdict(list)
    for event in events:
        grouped[event.get("region", "unknown")].append(event)
    regions = sorted(grouped)
    for region in regions[:40]:
        entries = sorted(grouped[region], key=lambda item: item.get("stopped_at", ""))
        lines = [f"*{escape_slack(region)}* ({len(entries)})"]
        shown = 0
        for event in entries:
            name = escape_slack(str(event.get("instance_name") or "(no name)")[:80])
            instance_id = escape_slack(event.get("instance_id", "unknown"))
            when = escape_slack(event.get("stopped_at", "unknown"))
            status = "stopped" if event.get("stop_status") == "ok" else "FAILED"
            line = f"• {name} ({instance_id}) — {when}, {event.get('age_hours', 0)}h — {status}"
            if len("\n".join(lines + [line])) > 2800 or shown >= 30:
                break
            lines.append(line)
            shown += 1
        if shown < len(entries):
            lines.append(f"… and {len(entries) - shown} more")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})
    if len(regions) > 40:
        omitted = sum(len(grouped[region]) for region in regions[40:])
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": f"… and {omitted} more instances in {len(regions) - 40} regions"}})
    return blocks


def post_slack(blocks: List[Dict], fallback_text: str) -> bool:
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        print("SLACK_WEBHOOK_URL not configured, skipping Slack notification")
        return False
    try:
        response = requests.post(webhook_url, json={"text": fallback_text, "blocks": blocks}, timeout=5)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as error:
        # Request exception text can include the secret webhook URL.
        print(f"Error sending Slack notification: {type(error).__name__}")
        return False
    except Exception as error:
        print(f"Unexpected error sending Slack notification: {type(error).__name__}")
        return False


def send_failure_alert(failures: List[str]) -> None:
    if not failures:
        return
    lines = [f"• {escape_slack(failure)[:180]}" for failure in failures[:15]]
    if len(failures) > 15:
        lines.append(f"… and {len(failures) - 15} more failures")
    lines.append("See CloudWatch Logs for /aws/lambda/ec2-watchman.")
    post_slack(
        [{"type": "header", "text": {"type": "plain_text", "text": "EC2 Watchman failure"}},
         {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}],
        f"EC2 Watchman: {len(failures)} failure(s)",
    )


def handle_sweep(event, context):
    regions = get_all_regions()
    print(f"Checking {len(regions)} regions for EC2 instances...")
    failures = []
    candidates = []
    region_summary = {}
    for region in regions:
        try:
            ec2 = boto3.client("ec2", region_name=region)
            paginator = ec2.get_paginator("describe_instances")
            selected = {}
            for page in paginator.paginate():
                for reservation in page["Reservations"]:
                    for instance in reservation["Instances"]:
                        if should_shutdown_instance(instance):
                            instance_id = instance["InstanceId"]
                            selected[instance_id] = {
                                "name": get_instance_name(instance),
                                "age_hours": get_instance_age_hours(instance),
                            }
                            print(f"[{region}] Instance {instance_id} marked for shutdown")
            if selected:
                candidates.append((region, ec2, selected))
            else:
                print(f"[{region}] No instances to shut down")
        except Exception as error:
            failures.append(f"{region}: describe_instances: {error}")
            print(f"Error processing region {region}: {error}")

    total_stopped = 0
    for region, ec2, selected in candidates:
        instance_ids = list(selected)
        try:
            response = ec2.stop_instances(InstanceIds=instance_ids)
            accepted = {entry["InstanceId"] for entry in response.get("StoppingInstances", [])}
        except Exception as error:
            accepted = set()
            failures.append(f"{region}: stop_instances: {error}")
            print(f"Error shutting down instances in {region}: {error}")
            stop_error = str(error)
        else:
            stop_error = "EC2 did not confirm stop request"
            if len(accepted) != len(instance_ids):
                failures.append(f"{region}: stop_instances confirmed {len(accepted)}/{len(instance_ids)} instances")

        for instance_id, details in selected.items():
            stopped_at = datetime.now(timezone.utc)
            status = "ok" if instance_id in accepted else "failed"
            try:
                record_shutdown_event(instance_id, details["name"], region,
                                      details["age_hours"], stopped_at, status,
                                      None if status == "ok" else stop_error)
            except Exception as error:
                failures.append(f"{region}: DynamoDB write for {instance_id}: {error}")
                print(f"Error recording shutdown event for {instance_id} in {region}: {error}")
            if status != "ok":
                continue
            total_stopped += 1
            region_summary[region] = region_summary.get(region, 0) + 1
            try:
                ec2.create_tags(Resources=[instance_id], Tags=[
                    {"Key": "watchman-stopped-at", "Value": utc_iso(stopped_at)}
                ])
            except Exception as error:
                failures.append(f"{region}: create_tags for {instance_id}: {error}")
                print(f"Error tagging instance {instance_id} in {region}: {error}")

    send_failure_alert(failures)
    return {"statusCode": 200, "body": json.dumps({
        "instances_shutdown": total_stopped,
        "regions_checked": len(regions),
        "regions_with_shutdowns": region_summary,
        "total_regions": len(regions),
        "failures": len(failures),
    })}


def handle_report(event, context):
    end = datetime.now(timezone.utc)
    start = read_watermark() or end - timedelta(days=7)
    if start > end:
        raise ValueError("Report watermark is in the future")
    events = query_events(start, end)
    blocks = build_digest_blocks(events, start, end)
    if not post_slack(blocks, f"EC2 Watchman shutdown report: {len(events)} events"):
        raise RuntimeError("Weekly shutdown report was not posted to Slack")
    write_watermark(end)
    return {"statusCode": 200, "body": json.dumps({
        "events_reported": len(events), "reported_through": utc_iso(end)
    })}


def lambda_handler(event, context):
    mode = event.get("mode", "sweep") if isinstance(event, dict) else "sweep"
    try:
        if mode == "report":
            return handle_report(event, context)
        return handle_sweep(event, context)
    except Exception as error:
        print(f"Error in lambda_handler ({mode}): {error}")
        send_failure_alert([f"{mode}: unhandled exception: {error}"])
        return {"statusCode": 500, "body": json.dumps({"error": str(error)})}
