#!/usr/bin/env python3
"""
Test suite for sippy_query.py

Validates argument parsing, filtering logic, and output formatting.
"""

import subprocess
import sys

# Add sippy plugin to path for imports
sys.path.insert(0, './plugins/edge-ocp-ci/skills/sippy')
import sippy_query


def run_command(args):
    """Run sippy_query.py with given arguments and return output."""
    cmd = ['./sippy_query.py', *args]  # safe: args are controlled in tests
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.CalledProcessError as e:
        return e.stdout, e.stderr, e.returncode


def test_topology_selection():
    """Test topology selection."""
    print("\nTesting topology selection...")

    # Test TNF
    stdout, _stderr, code = run_command(['--release=4.22', '--topology=tnf'])
    assert code == 0, "TNF topology should succeed"
    assert "Two-Node with Fencing" in stdout, "Should show TNF topology name"
    assert "DualReplica" in stdout, "Should show TNF feature gate"
    print("✅ TNF topology test passed")

    # Test TNA
    stdout, _stderr, code = run_command(['--release=4.22', '--topology=tna'])
    assert code == 0, "TNA topology should succeed"
    assert "Two-Node with Arbiter" in stdout, "Should show TNA topology name"
    assert "HighlyAvailableArbiter" in stdout, "Should show TNA feature gate"
    print("✅ TNA topology test passed")

    # Test SNO with test-scope=all
    stdout, _stderr, code = run_command(['--release=4.22', '--topology=sno', '--test-scope=all', '--output-level=job'])
    assert code == 0, "SNO with test-scope=all should succeed"
    assert "Single Node OpenShift" in stdout, "Should show SNO topology name"
    print("✅ SNO with test-scope=all test passed")


def test_sno_feature_restriction():
    """Test that SNO rejects feature-gated test scope."""
    print("\nTesting SNO feature gate restriction...")

    # SNO with test-scope=feature should fail
    stdout, stderr, code = run_command(['--release=4.22', '--topology=sno', '--test-scope=feature'])
    assert code != 0, "SNO with test-scope=feature should fail"
    assert "does not have feature-gated tests" in stderr, "Should show error message"
    print("✅ SNO feature gate restriction test passed")


def test_job_scope_filter():
    """Test job scope filtering."""
    print("\nTesting job scope filters...")

    # Main job only
    stdout, _stderr, code = run_command(['--release=4.22', '--topology=tnf', '--job-scope=main', '--output-level=job'])
    assert code == 0, "Main job scope should succeed"
    print("✅ Main job scope test passed")

    # All jobs
    stdout, _stderr, code = run_command(['--release=4.22', '--topology=tnf', '--job-scope=all', '--output-level=job'])
    assert code == 0, "All jobs scope should succeed"
    print("✅ All jobs scope test passed")


def test_output_levels():
    """Test job-level vs test-level output."""
    print("\nTesting output levels...")

    # Job-level output
    stdout, _stderr, code = run_command(['--release=4.22', '--topology=tnf', '--output-level=job'])
    assert code == 0, "Job-level output should succeed"
    assert "Output Level: JOB" in stdout, "Should show job output level"
    print("✅ Job-level output test passed")

    # Test-level output
    stdout, _stderr, code = run_command(['--release=4.22', '--topology=tnf', '--output-level=test'])
    assert code == 0, "Test-level output should succeed"
    assert "Output Level: TEST" in stdout, "Should show test output level"
    print("✅ Test-level output test passed")


def test_test_scope_filter():
    """Test test-scope filtering."""
    print("\nTesting test scope filters...")

    # Feature tests only
    stdout, _stderr, code = run_command(['--release=4.22', '--topology=tnf', '--test-scope=feature'])
    assert code == 0, "Feature test scope should succeed"
    assert "feature-gated tests only" in stdout, "Should indicate feature-gated filter"
    print("✅ Feature test scope test passed")

    # All tests
    stdout, _stderr, code = run_command(['--release=4.22', '--topology=tnf', '--test-scope=all'])
    assert code == 0, "All tests scope should succeed"
    print("✅ All tests scope test passed")


def test_network_stack_mode():
    """Test network stack breakdown vs overall."""
    print("\nTesting network stack modes...")

    # By network stack (default)
    stdout, _stderr, code = run_command(['--release=4.22', '--topology=tnf', '--by-network-stack', '--output-level=job'])
    assert code == 0, "Network stack breakdown should succeed"
    print("✅ Network stack breakdown test passed")

    # Overall summary
    stdout, _stderr, code = run_command(['--release=4.22', '--topology=tnf', '--overall', '--output-level=job'])
    assert code == 0, "Overall summary should succeed"
    print("✅ Overall summary test passed")


def test_all_topology():
    """Test querying all topologies."""
    print("\nTesting all topologies...")

    # All with feature tests (should exclude SNO)
    stdout, _stderr, code = run_command(['--release=4.22', '--topology=all', '--test-scope=feature'])
    assert code == 0, "All topologies with feature scope should succeed"
    assert "Two-Node with Fencing" in stdout, "Should include TNF"
    assert "Two-Node with Arbiter" in stdout, "Should include TNA"
    # SNO should be excluded when test-scope=feature
    print("✅ All topologies with feature scope test passed")


def test_variant_filtering():
    """Test variant-based filtering logic."""
    print("\nTesting variant-based filtering...")

    # Test topology variant matching
    job_tnf = {
        'name': 'test-job-tnf',
        'variants': ['Platform:metal', 'Topology:two-node-fencing', 'NetworkStack:ipv4']
    }
    job_tna = {
        'name': 'test-job-tna',
        'variants': ['Platform:metal', 'Topology:two-node-arbiter', 'NetworkStack:ipv6']
    }
    job_sno = {
        'name': 'test-job-sno',
        'variants': ['Platform:metal', 'Topology:single', 'NetworkStack:dual']
    }

    jobs = [job_tnf, job_tna, job_sno]

    # Test TNF filtering
    tnf_jobs = sippy_query.filter_jobs_by_topology(jobs, 'tnf', 'all')
    assert len(tnf_jobs) == 1, "Should filter to TNF jobs only"
    assert tnf_jobs[0]['name'] == 'test-job-tnf', "Should match TNF job"

    # Test TNA filtering
    tna_jobs = sippy_query.filter_jobs_by_topology(jobs, 'tna', 'all')
    assert len(tna_jobs) == 1, "Should filter to TNA jobs only"

    # Test SNO filtering
    sno_jobs = sippy_query.filter_jobs_by_topology(jobs, 'sno', 'all')
    assert len(sno_jobs) == 1, "Should filter to SNO jobs only"

    print("✅ Variant filtering test passed")


def test_sno_main_job_collection():
    """Test that SNO main job scope collects all non-excluded main jobs."""
    print("\nTesting SNO main job collection...")

    # Create test jobs: assisted-installer, plain, and excluded jobs
    job_sno_assisted = {
        'name': 'e2e-metal-assisted-sno-ipv4',
        'variants': ['Platform:metal', 'Topology:single', 'NetworkStack:ipv4', 'Upgrade:none']
    }
    job_sno_plain = {
        'name': 'e2e-metal-sno-ipv4',
        'variants': ['Platform:metal', 'Topology:single', 'NetworkStack:ipv4', 'Upgrade:none']
    }
    job_sno_excluded = {
        'name': 'e2e-metal-sno-ipv4-cert-rotation',
        'variants': ['Platform:metal', 'Topology:single', 'NetworkStack:ipv4', 'Upgrade:none']
    }

    jobs = [job_sno_assisted, job_sno_plain, job_sno_excluded]

    # Test SNO filtering with main scope - should get all non-excluded jobs
    sno_main_jobs = sippy_query.filter_jobs_by_topology(jobs, 'sno', 'main')
    assert len(sno_main_jobs) == 2, "Should filter to two SNO main jobs (both assisted and plain)"
    job_names = [j['name'] for j in sno_main_jobs]
    assert 'e2e-metal-assisted-sno-ipv4' in job_names, "Should include assisted job"
    assert 'e2e-metal-sno-ipv4' in job_names, "Should include plain job"
    assert 'e2e-metal-sno-ipv4-cert-rotation' not in job_names, "Should exclude cert-rotation job"

    print("✅ SNO main job collection test passed")


def test_network_stack_grouping():
    """Test network stack grouping by variants."""
    print("\nTesting network stack grouping...")

    jobs = [
        {'name': 'job1', 'variants': ['NetworkStack:ipv4']},
        {'name': 'job2', 'variants': ['NetworkStack:ipv6']},
        {'name': 'job3', 'variants': ['NetworkStack:dual']},
        {'name': 'job4', 'variants': ['NetworkStack:ipv4']},
    ]

    grouped = sippy_query.group_by_network_stack(jobs)

    assert len(grouped['ipv4']) == 2, "Should group IPv4 jobs"
    assert len(grouped['ipv6']) == 1, "Should group IPv6 jobs"
    assert len(grouped['dualstack']) == 1, "Should group dualstack jobs"

    print("✅ Network stack grouping test passed")




def test_multiple_releases():
    """Test multiple release querying."""
    print("\nTesting multiple releases...")

    stdout, _stderr, code = run_command(['--release=4.21,4.22', '--topology=tnf', '--output-level=job'])
    assert code == 0, "Multiple releases should succeed"
    assert "RELEASE 4.21" in stdout, "Should show 4.21 section"
    assert "RELEASE 4.22" in stdout, "Should show 4.22 section"

    print("✅ Multiple releases test passed")


def main():
    """Run all tests."""
    print("=" * 60)
    print("Running Sippy Query Script Tests")
    print("=" * 60)

    try:
        # Integration tests (require --release argument)
        print("\n=== Integration Tests (with --release) ===")
        test_topology_selection()
        test_sno_feature_restriction()
        test_job_scope_filter()
        test_output_levels()
        test_test_scope_filter()
        test_network_stack_mode()
        test_all_topology()
        test_multiple_releases()

        # Unit tests (test filtering logic)
        print("\n=== Unit Tests (filtering logic) ===")
        test_variant_filtering()
        test_sno_main_job_collection()
        test_network_stack_grouping()

        print("\n" + "=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)
        return 0

    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
