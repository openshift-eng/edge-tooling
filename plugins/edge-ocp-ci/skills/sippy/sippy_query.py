#!/usr/bin/env python3
"""
Sippy API query tool for edge topology promotion tests.

Queries Sippy API for edge topologies (TNF, TNA, SNO) and generates
status reports with network stack breakdown and GA readiness assessment.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import quote


class SippyQueryError(Exception):
    """Exception raised when Sippy API query fails."""
    pass


# Feature gate mapping by topology
FEATURE_GATES = {
    'tnf': 'OCPFeatureGate:DualReplica',
    'tna': 'OCPFeatureGate:HighlyAvailableArbiter',
    'sno': None,  # SNO has no feature gate
    'ha': None  # HA (standard 3-node) has no feature gate
}

# Topology full names
TOPOLOGY_NAMES = {
    'tnf': 'Two-Node with Fencing',
    'tna': 'Two-Node with Arbiter',
    'sno': 'Single Node OpenShift',
    'ha': 'Highly Available (3-node)'
}

# Network stack variants
NETWORK_STACKS = {
    'ipv4': 'IPv4',
    'ipv6': 'IPv6',
    'dualstack': 'DualStack'
}

# Status emojis
STATUS_EMOJI = {
    'pass': '✅',
    'warning': '🟡',
    'fail': '❌'
}

# GA readiness threshold (applies to promotion/feature-gated tests only)
GA_THRESHOLD = 95.0


def get_status_emoji(pass_rate: float) -> str:
    """Get emoji for pass rate."""
    if pass_rate >= GA_THRESHOLD:
        return STATUS_EMOJI['pass']
    elif pass_rate >= 90.0:
        return STATUS_EMOJI['warning']
    else:
        return STATUS_EMOJI['fail']


def query_sippy_api(release: str, endpoint: str, days: Optional[int] = None, filters: Optional[Dict] = None, test: Optional[str] = None) -> Dict:
    """Query Sippy API and return JSON response."""
    base_url = "https://sippy.dptools.openshift.org/api"
    url = f"{base_url}/{endpoint}?release={release}"
    if days:
        url += f"&days={days}"
    if filters:
        url += f"&filter={quote(json.dumps(filters))}"
    if test:
        url += f"&test={quote(test)}"

    # Add default parameters that web UI uses
    url += "&period=default&collapse=true"

    try:
        req = Request(url)
        req.add_header('User-Agent', 'edge-ocp-ci-sippy/1.0')
        with urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode('utf-8'))
    except (URLError, HTTPError) as e:
        raise SippyQueryError(f"Error querying Sippy API: {e}")
    except json.JSONDecodeError as e:
        raise SippyQueryError(f"Error parsing Sippy API response: {e}")


def get_topology_variant(topology: str) -> Optional[str]:
    """Get topology variant string for filtering."""
    variants = {
        'tnf': 'Topology:two-node-fencing',
        'tna': 'Topology:two-node-arbiter',
        'sno': 'Topology:single',
        'ha': 'Topology:ha'
    }
    return variants.get(topology)


def filter_jobs_by_topology(jobs: List[Dict], topology: str, job_scope: str) -> List[Dict]:
    """Filter jobs by topology using variants."""
    topology_variant = get_topology_variant(topology)
    if not topology_variant:
        return []

    filtered = []
    for job in jobs:
        variants = job.get('variants') or []
        # Filter by topology AND Platform:metal
        if topology_variant in variants and 'Platform:metal' in variants:
            # If job_scope is main, filter to only the base ovn-ipv4 job
            if job_scope == 'main':
                # Main job has NetworkStack:ipv4 and no special features/upgrades
                if 'NetworkStack:ipv4' in variants and 'Upgrade:none' in variants:
                    # Exclude extended, serial, degraded, recovery, upgrade variants
                    name = job.get('name', '').lower()
                    exclude_patterns = ['extended', 'serial', 'degraded', 'recovery', 'upgrade', 'with-worker', '-rt-', 'cert-rotation', 'kpis', 'day2', 'kube-api-late']
                    if not any(x in name for x in exclude_patterns):
                        filtered.append(job)
            else:
                filtered.append(job)

    return filtered




def group_by_network_stack(items: List[Dict]) -> Dict[str, List[Dict]]:
    """Group jobs or tests by network stack variant."""
    stacks = {stack: [] for stack in NETWORK_STACKS.keys()}

    for item in items:
        variants = item.get('variants') or []
        if not variants:
            # Fallback to name parsing if no variants
            name = item.get('name', '').lower()
            if 'ipv6' in name:
                stacks['ipv6'].append(item)
            elif 'dualstack' in name or 'dual-stack' in name:
                stacks['dualstack'].append(item)
            else:
                stacks['ipv4'].append(item)
        else:
            # Use variants for accurate filtering
            if 'NetworkStack:ipv6' in variants:
                stacks['ipv6'].append(item)
            elif 'NetworkStack:dual' in variants:
                stacks['dualstack'].append(item)
            else:
                # Default to ipv4 (NetworkStack:ipv4 or unspecified)
                stacks['ipv4'].append(item)

    return stacks


def calculate_pass_rate(item: Dict) -> Tuple[float, int]:
    """Calculate pass rate and run count from Sippy data."""
    # Sippy API returns current_pass_percentage (already a percentage) and current_runs
    pass_rate = item.get('current_pass_percentage', 0.0)
    runs = item.get('current_runs', 0)
    return pass_rate, runs


def format_job_output(jobs: List[Dict], topology: str, by_network_stack: bool) -> str:
    """Format job-level output."""
    output = []

    if by_network_stack:
        stacks = group_by_network_stack(jobs)
        for stack_key, stack_jobs in stacks.items():
            if not stack_jobs:
                continue

            stack_name = NETWORK_STACKS[stack_key]
            output.append(f"\n{stack_name} Lane Jobs:")

            for job in stack_jobs:
                pass_rate, runs = calculate_pass_rate(job)
                emoji = get_status_emoji(pass_rate)
                job_name = job.get('name', '')
                # Simplify long periodic job names
                if 'periodic-ci-openshift-release-master-nightly-' in job_name:
                    job_name = job_name.split('periodic-ci-openshift-release-master-nightly-')[-1]
                output.append(f"  {emoji} {job_name}: {pass_rate:.0f}% ({runs} runs)")
    else:
        # Overall summary - weighted average by runs
        total_weighted_pass = sum(job.get('current_pass_percentage', 0) * job.get('current_runs', 0) for job in jobs)
        total_runs = sum(job.get('current_runs', 0) for job in jobs)
        overall_rate = (total_weighted_pass / total_runs) if total_runs > 0 else 0.0

        threshold_status = "meets" if overall_rate >= GA_THRESHOLD else "below"
        output.append(f"\nOverall Pass Rate: {overall_rate:.0f}% ({threshold_status} {GA_THRESHOLD:.0f}% GA threshold)")
        output.append(f"Total Runs: {total_runs}")

    return '\n'.join(output)


def format_test_output(tests_by_stack: Dict[str, List[Dict]], topology: str, by_network_stack: bool, test_scope: str) -> str:
    """Format test-level output.

    Args:
        tests_by_stack: Dict mapping network stack ('ipv4', 'ipv6', 'dualstack') to list of tests
        topology: Topology name
        by_network_stack: Whether to show breakdown by network stack
        test_scope: Test scope filter
    """
    output = []

    if by_network_stack:
        for stack_key in ['ipv4', 'ipv6', 'dualstack']:
            stack_tests = tests_by_stack.get(stack_key, [])
            if not stack_tests:
                continue

            stack_name = NETWORK_STACKS[stack_key]
            output.append(f"\n{stack_name} Lane Tests:")

            for test in sorted(stack_tests, key=lambda t: t.get('name', '')):
                pass_rate, runs = calculate_pass_rate(test)
                emoji = get_status_emoji(pass_rate)
                test_name = test.get('name', '')
                output.append(f"  {emoji} {test_name}: {pass_rate:.0f}% ({runs} runs)")
    else:
        # Overall summary - weighted average across all stacks
        all_tests = []
        for stack_tests in tests_by_stack.values():
            all_tests.extend(stack_tests)

        total_weighted_pass = sum(test.get('current_pass_percentage', 0) * test.get('current_runs', 0) for test in all_tests)
        total_runs = sum(test.get('current_runs', 0) for test in all_tests)
        overall_rate = (total_weighted_pass / total_runs) if total_runs > 0 else 0.0

        threshold_status = "meets" if overall_rate >= GA_THRESHOLD else "below"
        output.append(f"\nOverall Pass Rate: {overall_rate:.0f}% ({threshold_status} {GA_THRESHOLD:.0f}% GA threshold)")
        output.append(f"Total Runs: {total_runs}")

    return '\n'.join(output)


def generate_header(topology: str, output_level: str, job_scope: str, test_scope: str,
                    by_network_stack: bool, overall: bool) -> str:
    """Generate report header."""
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M %Z")

    header = []
    header.append("╔══════════════════════════════════════════════════════════════╗")

    if overall:
        title = "Main Job Overall Status" if job_scope == 'main' else "Overall Status"
    elif by_network_stack:
        if output_level == 'job':
            title = f"{TOPOLOGY_NAMES[topology]} Jobs by Network Stack"
        else:
            title = f"{TOPOLOGY_NAMES[topology]} Tests by Network Stack"
    else:
        title = f"{TOPOLOGY_NAMES[topology]} Status"

    header.append(f"║  {title:<60}║")
    header.append(f"║  Output Level: {output_level.upper():<47}║")

    if job_scope == 'main':
        header.append(f"║  Job: e2e-metal-ovn-ipv4{' ' * 35}║")

    feature_gate = FEATURE_GATES.get(topology)
    if feature_gate and test_scope == 'feature':
        header.append(f"║  Feature Gate: {feature_gate:<44}║")

    if output_level == 'test':
        scope_text = "feature-gated tests only" if test_scope == 'feature' else "all tests"
        header.append(f"║  Test Scope: {scope_text:<47}║")

    header.append(f"║  Generated: {timestamp:<48}║")
    header.append("╚══════════════════════════════════════════════════════════════╝")

    return '\n'.join(header)


def validate_args(args: argparse.Namespace) -> None:
    """Validate argument combinations."""
    # SNO and HA restriction: cannot use test-scope=feature with test-level output
    if args.test_scope == 'feature' and args.topology in ['sno', 'ha'] and args.output_level == 'test':
        print(f"Error: Topology {args.topology} does not have feature-gated tests. "
              "Use --test-scope=all or --output-level=job", file=sys.stderr)
        sys.exit(1)

    # test-scope only applies to test output level
    if args.output_level == 'job' and args.test_scope == 'feature':
        print("Warning: --test-scope is ignored for job-level output", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description='Query Sippy for edge topology promotion test status'
    )
    parser.add_argument(
        '--topology',
        choices=['tnf', 'tna', 'sno', 'ha', 'edge', 'all'],
        default='edge',
        help='Topology to query: edge (tnf+tna+sno), all (edge+ha), or individual (default: edge)'
    )
    parser.add_argument(
        '--output-level',
        choices=['job', 'test'],
        default='job',
        help='Output granularity (default: job)'
    )
    parser.add_argument(
        '--by-network-stack',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Detailed breakdown by IPv4/IPv6/DualStack (default: true)'
    )
    parser.add_argument(
        '--overall',
        action='store_true',
        help='Overall GA readiness summary only'
    )
    parser.add_argument(
        '--all-reports',
        action='store_true',
        help='Both network stack and overall reports combined'
    )
    parser.add_argument(
        '--job-scope',
        choices=['main', 'all'],
        default='all',
        help='Job-level filter: main ovn-ipv4 job or all jobs (default: all)'
    )
    parser.add_argument(
        '--test-scope',
        choices=['feature', 'all'],
        default='feature',
        help='Test-level filter: feature-gated or all tests (default: feature)'
    )
    parser.add_argument(
        '--release',
        help='OCP release version(s) - comma-separated for multiple (e.g., "4.21,4.22,4.23")'
    )
    parser.add_argument(
        '--days',
        type=int,
        help='Number of days of history to query (default: 7)'
    )

    args = parser.parse_args()

    # If user specified test-scope but not output-level, default to test-level output
    test_scope_provided = any('--test-scope' in arg for arg in sys.argv)
    output_level_provided = any('--output-level' in arg for arg in sys.argv)
    if test_scope_provided and not output_level_provided:
        args.output_level = 'test'

    # Prompt for release if not provided
    if not args.release:
        release_pattern = re.compile(r'^\d+\.\d+(?:,\s*\d+\.\d+)*$')
        max_attempts = 3
        for attempt in range(max_attempts):
            print("Which OCP release(s) do you want to query?")
            print("Examples: 4.22 or 4.21,4.22,4.23")
            args.release = input("Release(s): ").strip()
            if not args.release:
                print("Error: Release is required", file=sys.stderr)
                sys.exit(1)
            if release_pattern.match(args.release):
                break
            print("Error: Invalid release format. Expected format like '4.22' or '4.21,4.22,4.23'", file=sys.stderr)
            if attempt == max_attempts - 1:
                print("Error: Too many invalid attempts", file=sys.stderr)
                sys.exit(1)
            args.release = None

    # Validate arguments
    validate_args(args)

    # Override by-network-stack if --overall is specified
    if args.overall:
        args.by_network_stack = False

    # Determine which topologies to query
    if args.topology == 'all':
        topologies = ['tnf', 'tna', 'sno', 'ha']
    elif args.topology == 'edge':
        topologies = ['tnf', 'tna', 'sno']
    else:
        topologies = [args.topology]

    # Filter out SNO and HA if test-scope=feature and test-level output (they don't have feature gates)
    if args.test_scope == 'feature' and args.output_level == 'test':
        topologies = [t for t in topologies if t not in ['sno', 'ha']]

    # Parse releases (support comma-separated list)
    releases = [r.strip() for r in args.release.split(',')]

    # Query and process each release
    for release in releases:
        if len(releases) > 1:
            print(f"\n{'=' * 64}")
            print(f"RELEASE {release}")
            print(f"{'=' * 64}")

        # Query and process each topology
        for topology in topologies:
            print()
            print(f"=== {TOPOLOGY_NAMES[topology].upper()} ===")
            print(generate_header(topology, args.output_level, args.job_scope,
                                args.test_scope, args.by_network_stack, args.overall))

            # Build topology filter
            topology_variant = get_topology_variant(topology)
            if not topology_variant:
                print(f"\nError: Unknown topology {topology}")
                continue

            # Query Sippy API with topology filter
            if args.output_level == 'job':
                # For jobs: query all jobs once and filter in Python (variants are in response)
                try:
                    data = query_sippy_api(release, "jobs", args.days)
                except SippyQueryError as e:
                    print(f"{e}", file=sys.stderr)
                    continue

                if not isinstance(data, list):
                    print(f"Error: Unexpected Sippy API response format for {release}: {type(data)}", file=sys.stderr)
                    continue

                filtered_items = filter_jobs_by_topology(data, topology, args.job_scope)
                if not filtered_items:
                    print(f"\nNo jobs found for topology {topology}")
                    continue
                output = format_job_output(filtered_items, topology, args.by_network_stack)
                print(output)
            else:
                # For tests: query once per network stack with combined topology+network filters
                tests_by_stack = {'ipv4': [], 'ipv6': [], 'dualstack': []}

                # Query each network stack separately
                for stack_key, stack_variant in [('ipv4', 'NetworkStack:ipv4'),
                                                   ('ipv6', 'NetworkStack:ipv6'),
                                                   ('dualstack', 'NetworkStack:dual')]:
                    # Build filter with topology + network stack + platform:metal
                    filter_items = [
                        {'columnField': 'variants', 'operatorValue': 'has entry', 'value': topology_variant},
                        {'columnField': 'variants', 'operatorValue': 'has entry', 'value': stack_variant},
                        {'columnField': 'variants', 'operatorValue': 'has entry', 'value': 'Platform:metal'}
                    ]

                    # Add feature gate filter if needed
                    if args.test_scope == 'feature':
                        feature_gate = FEATURE_GATES.get(topology)
                        if feature_gate:
                            filter_items.append({
                                'columnField': 'name',
                                'operatorValue': 'contains',
                                'value': feature_gate
                            })

                    filters = {
                        'items': filter_items,
                        'linkOperator': 'and'
                    }

                    try:
                        tests_by_stack[stack_key] = query_sippy_api(release, "tests", args.days, filters=filters)
                    except SippyQueryError as e:
                        print(f"{e}", file=sys.stderr)
                        tests_by_stack[stack_key] = []

                    if not isinstance(tests_by_stack[stack_key], list):
                        print(f"Error: Unexpected Sippy API response for {stack_key}: {type(tests_by_stack[stack_key])}", file=sys.stderr)
                        tests_by_stack[stack_key] = []

                # Check if we have any tests
                total_tests = sum(len(tests) for tests in tests_by_stack.values())
                if total_tests == 0:
                    print(f"\nNo tests found for topology {topology}")
                    continue

                output = format_test_output(tests_by_stack, topology, args.by_network_stack, args.test_scope)
                print(output)


if __name__ == '__main__':
    main()
