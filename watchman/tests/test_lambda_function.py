"""Offline tests for Watchman's scheduled sweep and report."""

import importlib.util
import json
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


class Expression:
    def __init__(self, *parts):
        self.parts = list(parts)

    def __and__(self, other):
        return Expression(*(self.parts + other.parts))


class Key:
    def __init__(self, name):
        self.name = name

    def eq(self, value):
        return Expression((self.name, "eq", value))

    def between(self, lower, upper):
        return Expression((self.name, "between", lower, upper))


boto3 = types.ModuleType("boto3")
dynamodb = types.ModuleType("boto3.dynamodb")
conditions = types.ModuleType("boto3.dynamodb.conditions")
conditions.Key = Key
requests = types.ModuleType("requests")
requests.exceptions = types.SimpleNamespace(RequestException=Exception)
with mock.patch.dict(sys.modules, {
    "boto3": boto3, "boto3.dynamodb": dynamodb,
    "boto3.dynamodb.conditions": conditions, "requests": requests,
}):
    spec = importlib.util.spec_from_file_location(
        "watchman_lambda", Path(__file__).parents[1] / "lambda_function.py"
    )
    watchman = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watchman)


UTC = timezone.utc
START = datetime(2026, 9, 30, 16, tzinfo=UTC)


class FakeTable:
    def __init__(self, items=None):
        self.items = items or []
        self.calls = []
        self.writes = []
        self.watermark = None

    def put_item(self, **kwargs):
        self.writes.append(kwargs["Item"])

    def get_item(self, **kwargs):
        self.calls.append(kwargs)
        return {"Item": {"reported_through": self.watermark}} if self.watermark else {}

    def update_item(self, **kwargs):
        self.calls.append(kwargs)
        self.watermark = kwargs["ExpressionAttributeValues"][":through"]

    def query(self, **kwargs):
        self.calls.append(kwargs)
        partition, bounds = kwargs["KeyConditionExpression"].parts
        self_partition = partition[2]
        lower, upper = bounds[2:]
        matches = [item for item in self.items
                   if item["pk"] == self_partition and lower <= item["sk"] <= upper]
        offset = kwargs.get("ExclusiveStartKey", {}).get("offset", 0)
        page = {"Items": matches[offset:offset + 1]}
        if offset + 1 < len(matches):
            page["LastEvaluatedKey"] = {"offset": offset + 1}
        return page


class WatchmanTests(unittest.TestCase):
    def test_record_event_has_ttl_and_status(self):
        table = FakeTable()
        with mock.patch.object(watchman, "shutdown_table", return_value=table), \
             mock.patch.dict(os.environ, {"SHUTDOWN_RETENTION_DAYS": "35"}):
            watchman.record_shutdown_event("i-1", "dev", "us-east-1", 14.25, START, "ok")
            watchman.record_shutdown_event("i-2", "i-2", "us-west-2", 20, START, "failed", "Access denied")
        self.assertEqual(table.writes[0]["pk"], "shutdown#2026-09")
        self.assertEqual(table.writes[0]["expires_at"], int(START.timestamp()) + 35 * 86400)
        self.assertEqual(table.writes[1]["instance_name"], "")
        self.assertEqual(table.writes[1]["error"], "Access denied")

    def test_watermark_is_consistent_and_persistent(self):
        table = FakeTable()
        with mock.patch.object(watchman, "shutdown_table", return_value=table):
            self.assertIsNone(watchman.read_watermark())
            watchman.write_watermark(START)
            self.assertEqual(watchman.read_watermark(), START)
        self.assertTrue(table.calls[0]["ConsistentRead"])
        self.assertEqual(table.calls[1]["Key"], {"pk": "meta", "sk": "last_report"})

    def test_report_window_first_run_and_watermark(self):
        for watermark in (None, START):
            with self.subTest(watermark=watermark), \
                 mock.patch.object(watchman, "read_watermark", return_value=watermark), \
                 mock.patch.object(watchman, "query_events", return_value=[]) as query, \
                 mock.patch.object(watchman, "post_slack", return_value=True), \
                 mock.patch.object(watchman, "write_watermark") as write:
                result = watchman.handle_report({}, None)
                start, end = query.call_args.args
                self.assertEqual(start, watermark or end - timedelta(days=7))
                self.assertEqual(result["statusCode"], 200)
                write.assert_called_once_with(end)

    def test_failed_post_does_not_advance_watermark(self):
        with mock.patch.object(watchman, "read_watermark", return_value=START), \
             mock.patch.object(watchman, "query_events", return_value=[]), \
             mock.patch.object(watchman, "post_slack", return_value=False), \
             mock.patch.object(watchman, "write_watermark") as write:
            with self.assertRaises(RuntimeError):
                watchman.handle_report({}, None)
            write.assert_not_called()

    def test_post_slack_sends_block_payload(self):
        response = mock.Mock()
        with mock.patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://example.invalid/hook"}), \
             mock.patch.object(watchman.requests, "post", return_value=response, create=True) as post:
            self.assertTrue(watchman.post_slack([{"type": "section"}], "fallback"))
        self.assertEqual(post.call_args.kwargs["json"]["text"], "fallback")
        self.assertEqual(post.call_args.kwargs["timeout"], 5)
        response.raise_for_status.assert_called_once()

    def test_query_crosses_month_and_paginates_without_upper_boundary(self):
        events = []
        for when, instance_id in (
            (START, "i-1"), (START + timedelta(minutes=1), "i-2"),
            (START + timedelta(days=1), "i-3"), (START + timedelta(days=1, minutes=1), "i-4"),
        ):
            stamp = watchman.utc_iso(when)
            events.append({"pk": f"shutdown#{stamp[:7]}", "sk": f"{stamp}#{instance_id}"})
        table = FakeTable(events)
        with mock.patch.object(watchman, "shutdown_table", return_value=table):
            result = watchman.query_events(START, START + timedelta(days=1, minutes=1))
        self.assertEqual(len(result), 3)
        self.assertEqual(len(table.calls), 3)  # Two September pages and one October page
        self.assertTrue(all(call["ConsistentRead"] for call in table.calls))

    def test_digest_empty_failed_and_oversized(self):
        empty = watchman.build_digest_blocks([], START, START + timedelta(days=7))
        self.assertIn("No instances stopped", empty[2]["text"]["text"])
        events = [{
            "region": f"us-test-{region}", "instance_id": f"i-{number}",
            "instance_name": "<unsafe>&", "stopped_at": watchman.utc_iso(START),
            "age_hours": 12.5, "stop_status": "failed" if number == 0 else "ok",
        } for region in range(45) for number in range(45)]
        blocks = watchman.build_digest_blocks(events, START, START + timedelta(days=7))
        self.assertLessEqual(len(blocks), 50)
        self.assertTrue(all(len(block["text"]["text"]) <= 3000
                            for block in blocks if block["type"] == "section"))
        self.assertIn("FAILED", blocks[2]["text"]["text"])
        self.assertIn("&lt;unsafe&gt;&amp;", blocks[2]["text"]["text"])
        self.assertIn("more instances", blocks[-1]["text"]["text"])

    def test_sweep_aggregates_stop_and_record_failures(self):
        old = START - timedelta(days=1)
        instance = {"InstanceId": "i-1", "LaunchTime": old,
                    "State": {"Name": "running"}, "Tags": [{"Key": "Name", "Value": "dev"}]}
        ec2 = mock.Mock()
        ec2.get_paginator.return_value.paginate.return_value = [
            {"Reservations": [{"Instances": [instance]}]}]
        ec2.stop_instances.side_effect = RuntimeError("stop denied")
        with mock.patch.object(watchman, "get_all_regions", return_value=["us-east-1"]), \
             mock.patch.object(watchman.boto3, "client", return_value=ec2, create=True), \
             mock.patch.object(watchman, "record_shutdown_event", side_effect=RuntimeError("ddb denied")) as record, \
             mock.patch.object(watchman, "post_slack", return_value=True) as post:
            result = watchman.handle_sweep({}, None)
        self.assertEqual(json.loads(result["body"])["failures"], 2)
        self.assertEqual(record.call_args.args[5], "failed")
        post.assert_called_once()
        alert = post.call_args.args[0][1]["text"]["text"]
        self.assertIn("stop_instances", alert)
        self.assertIn("DynamoDB write", alert)

    def test_successful_sweep_records_and_tags_without_slack(self):
        instance = {"InstanceId": "i-1", "LaunchTime": START - timedelta(days=1),
                    "State": {"Name": "running"}, "Tags": []}
        ec2 = mock.Mock()
        ec2.get_paginator.return_value.paginate.return_value = [
            {"Reservations": [{"Instances": [instance]}]}]
        ec2.stop_instances.return_value = {"StoppingInstances": [{"InstanceId": "i-1"}]}
        with mock.patch.object(watchman, "get_all_regions", return_value=["us-east-1"]), \
             mock.patch.object(watchman.boto3, "client", return_value=ec2, create=True), \
             mock.patch.object(watchman, "record_shutdown_event") as record, \
             mock.patch.object(watchman, "post_slack") as post:
            result = watchman.handle_sweep({}, None)
        self.assertEqual(json.loads(result["body"])["instances_shutdown"], 1)
        self.assertEqual(record.call_args.args[5], "ok")
        ec2.create_tags.assert_called_once()
        post.assert_not_called()

    def test_region_failure_sends_one_alert(self):
        ec2 = mock.Mock()
        ec2.get_paginator.side_effect = RuntimeError("describe denied")
        with mock.patch.object(watchman, "get_all_regions", return_value=["us-east-1", "us-west-2"]), \
             mock.patch.object(watchman.boto3, "client", return_value=ec2, create=True), \
             mock.patch.object(watchman, "post_slack", return_value=True) as post:
            result = watchman.handle_sweep({}, None)
        self.assertEqual(json.loads(result["body"])["failures"], 2)
        post.assert_called_once()

    def test_dispatch_defaults_to_sweep(self):
        with mock.patch.object(watchman, "handle_sweep", return_value={"statusCode": 200}) as sweep, \
             mock.patch.object(watchman, "handle_report", return_value={"statusCode": 200}) as report:
            watchman.lambda_handler(None, None)
            watchman.lambda_handler({"mode": "invalid"}, None)
            watchman.lambda_handler({"mode": "report"}, None)
        self.assertEqual(sweep.call_count, 2)
        report.assert_called_once()


if __name__ == "__main__":
    unittest.main()
