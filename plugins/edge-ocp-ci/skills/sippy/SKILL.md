---
name: sippy
description: Query Sippy API for edge topology health monitoring (TNF, TNA, SNO)
allowed-tools:
  - Bash
  - Read
  - AskUserQuestion
user-invocable: true
---

# Edge OCP CI: Sippy Status

Generate promotion status reports from Sippy for edge topologies (TNF, TNA, SNO).

## Task

Query Sippy API to generate status reports for edge topologies (TNF, TNA, SNO), showing health across different network stacks and overall GA readiness. Reports can be at job level or test level. Feature-gated test filtering is available for TNF and TNA.

## Instructions

**IMPORTANT:** When invoked without arguments or with "prompt me for parameters", use AskUserQuestion to prompt the user for all parameters, then run `./sippy_query.py` with those parameters. When invoked with specific arguments, run the script directly with those arguments.

**How to execute:**
1. If no arguments provided or user asks to be prompted:
   - Use AskUserQuestion to prompt for: release, topology, output-level
   - If output-level=test, also prompt for: test-scope
   - Always prompt for: job-scope, format (by-network-stack vs overall), days
   - Skip irrelevant questions based on prior answers (e.g., don't ask test-scope for job-level output)
2. Run `./sippy_query.py` with the collected parameters
3. Display the complete output to the user

**Available arguments:**

- `--release=<version>`: **REQUIRED** - OCP release(s) (e.g., `4.22` or `4.21,4.22,4.23`)
- `--topology=<type>`: Topology to query (`tnf`, `tna`, `sno`, `edge`, `all`; default: `edge`)
  - `edge` = TNF + TNA + SNO
  - `all` = edge + HA
- `--output-level=<level>`: Granularity (`job`, `test`; default: `job`)
  - `job` = shows job-level pass rates with network stack breakdown
  - `test` = shows per-test statistics with accurate network stack breakdown
- `--test-scope=<scope>`: Test filter (`feature`, `all`; default: `feature`)
  - `feature` = promotion tests only (TNF/TNA only - SNO has no promotion tests)
  - `all` = all tests from topology jobs
- `--job-scope=<scope>`: Job filter (`main`, `all`; default: `all`)
- `--by-network-stack`: Show IPv4/IPv6/DualStack breakdown (default: enabled)
- `--overall`: Show only overall summary (disables network stack breakdown)
- `--days=<N>`: Number of days of history (default: 7)

**Important notes:**

- **SNO restriction**: SNO only works with `--test-scope=all` or `--output-level=job` (no promotion tests)
- **Promotion tests** (`--test-scope=feature`): Only run in IPv4 jobs, so you'll only see IPv4 lane
- **All tests** (`--test-scope=all`): Tests run across IPv4, IPv6, and DualStack lanes
- **Network stack breakdown**: Enabled by default, shows separate sections for each network stack
- **Output format**: Uses emojis - ✅ (≥95%), 🟡 (90-95%), ❌ (<90%)
- **GA threshold**: 95% pass rate (only applies to promotion tests)

## Implementation

**Script:** `sippy_query.py`

Run the query script directly:

```bash
./plugins/edge-ocp-ci/skills/sippy/sippy_query.py [arguments]
```

The script implements argument parsing, validation, feature gate mapping, and output formatting. **Status:** Both job-level and test-level output are fully functional with real Sippy API data.

**Test Suite:** `test_sippy.py`

Validate the implementation:

```bash
./plugins/edge-ocp-ci/skills/sippy/test_sippy.py
```

Tests cover:
- Argument parsing and validation
- Topology selection (TNF, TNA, SNO, all)
- SNO feature gate restriction
- Job scope filtering (main vs all)
- Test scope filtering (feature vs all)
- Output level selection (job vs test)
- Network stack breakdown vs overall summary

## Important

- **Job-level vs Test-level**: 
  - Job-level (`--output-level=job`): Shows pass rates for entire jobs (default)
  - Test-level (`--output-level=test`): Shows pass rates for individual tests
- **Feature gate filtering is test-level**: When using `--test-scope=feature`, this filters individual tests by their OCPFeatureGate label
- **Job scope affects both levels**: `--job-scope` filters which jobs to query, applies to both job and test output
- **Network stack breakdown**: Works at both job and test level using variant filters (IPv4, IPv6, DualStack)
- **Default shows all topologies with feature gates**: `--topology=all` queries TNF, TNA, and SNO (but SNO excluded if `--test-scope=feature`)
- **SNO has no feature gate**: SNO can be queried for job-level or test-level with `--test-scope=all`, but not with `--test-scope=feature`
- **Only TNF and TNA have feature gates**: Only these topologies have OCPFeatureGate promotion tests
- GA readiness threshold is 95% pass rate
- Results complement TestGrid data with aggregated pass rates

## Example Execution

### Test-level output (default)

```text
Input: /edge-ocp-ci:sippy --release=4.22

Output (default: test-level, all topologies, all jobs, feature tests only, by network stack):
╔══════════════════════════════════════════════════════════════╗
║  Edge Topology Feature Tests by Network Stack              ║
║  Output Level: TEST                                          ║
║  Generated: 2026-04-17 08:39 EDT                            ║
╚══════════════════════════════════════════════════════════════╝

=== TNF (Two-Node with Fencing) ===
Feature Gate: OCPFeatureGate:DualReplica
Test Scope: feature-gated tests only

IPv4 Lane Tests:
  ✅ [OCPFeatureGate:DualReplica] topology should have BareMetalHost operational...: 100% (94 runs)
  ✅ [OCPFeatureGate:DualReplica] etcd recovery should recover from ungraceful...: 100% (38 runs)
  🟡 [OCPFeatureGate:DualReplica] etcd recovery should recover from etcd process crash: 92% (36 runs)
...

=== TNA (Two-Node with Arbiter) ===
Feature Gate: OCPFeatureGate:HighlyAvailableArbiter
Test Scope: feature-gated tests only

IPv4 Lane Tests:
  ✅ [OCPFeatureGate:HighlyAvailableArbiter] arbiter node should be reachable: 99% (120 runs)
...
```

```text
Input: /edge-ocp-ci:sippy --release=4.22 --topology=tnf --job-scope=main --test-scope=all

Output (test-level, TNF only, main job only, all tests including non-feature):
╔══════════════════════════════════════════════════════════════╗
║  TNF Tests - Main Job Only                                  ║
║  Output Level: TEST                                          ║
║  Job: e2e-metal-ovn-ipv4                                     ║
║  Feature Gate: OCPFeatureGate:DualReplica                   ║
║  Test Scope: all tests                                       ║
║  Generated: 2026-04-17 08:39 EDT                            ║
╚══════════════════════════════════════════════════════════════╝

IPv4 Lane Tests:
  ✅ [OCPFeatureGate:DualReplica] topology should have BareMetalHost operational...: 100% (94 runs)
  ✅ etcd should be healthy: 98% (120 runs)
  ✅ [OCPFeatureGate:DualReplica] etcd recovery should recover from ungraceful...: 100% (38 runs)
...

Overall: 93% pass rate (below 95% GA threshold)
```

### Job-level output

```text
Input: /edge-ocp-ci:sippy --release=4.22 --output-level=job --topology=tnf

Output (job-level, TNF only, all jobs):
╔══════════════════════════════════════════════════════════════╗
║  TNF Jobs by Network Stack                                  ║
║  Output Level: JOB                                           ║
║  Feature Gate: OCPFeatureGate:DualReplica                   ║
║  Generated: 2026-04-17 08:39 EDT                            ║
╚══════════════════════════════════════════════════════════════╝

IPv4 Lane Jobs:
  ✅ e2e-metal-ovn-ipv4: 96% (480 runs)
  🟡 e2e-metal-ovn-ipv4-serial: 92% (240 runs)

IPv6 Lane Jobs:
  ❌ e2e-metal-ovn-ipv6: 89% (360 runs)
  🟡 e2e-metal-ovn-ipv6-serial: 91% (180 runs)

DualStack Lane Jobs:
  ✅ e2e-metal-ovn-dualstack: 97% (420 runs)
...

Overall: 94% pass rate (below 95% GA threshold)
```

```text
Input: /edge-ocp-ci:sippy --release=4.21,4.22,4.23 --output-level=job --job-scope=main --overall

Output (job-level, main job only, overall summary, multiple releases):
================================================================
RELEASE 4.21
================================================================

=== TNF ===
Overall Pass Rate: 94% (below 95% GA threshold)
Total Runs: 420

=== TNA ===
Overall Pass Rate: 96% (meets 95% GA threshold)
Total Runs: 480

================================================================
RELEASE 4.22
================================================================

=== TNF ===
Overall Pass Rate: 96% (meets 95% GA threshold)
Total Runs: 480

=== TNA ===
Overall Pass Rate: 97% (meets 95% GA threshold)
Total Runs: 520

================================================================
RELEASE 4.23
================================================================

=== TNF ===
Overall Pass Rate: 95% (meets 95% GA threshold)
Total Runs: 510

=== TNA ===
Overall Pass Rate: 98% (meets 95% GA threshold)
Total Runs: 540
```

```text
Input: /edge-ocp-ci:sippy --release=4.22 --topology=sno --test-scope=feature

Output:
Error: Topology sno does not have feature-gated tests. Use --test-scope=all or --output-level=job
```

```text
Input: /edge-ocp-ci:sippy --release=4.22 --output-level=job --topology=sno

Output (job-level, SNO works without feature gate requirement):
╔══════════════════════════════════════════════════════════════╗
║  SNO Jobs by Network Stack                                  ║
║  Output Level: JOB                                           ║
║  Generated: 2026-04-17 08:39 EDT                            ║
╚══════════════════════════════════════════════════════════════╝

IPv4 Lane Jobs:
  ✅ e2e-metal-sno: 98% (720 runs)
...
```

```text
Input: /edge-ocp-ci:sippy --release=4.22 --topology=sno --test-scope=all

Output (test-level with all tests, SNO works):
╔══════════════════════════════════════════════════════════════╗
║  SNO Tests by Network Stack                                 ║
║  Output Level: TEST                                          ║
║  Test Scope: all tests                                       ║
║  Generated: 2026-04-17 08:39 EDT                            ║
╚══════════════════════════════════════════════════════════════╝

IPv4 Lane Tests:
  ✅ etcd should be healthy: 99% (180 runs)
  ✅ operator should not degrade: 97% (180 runs)
...
```

## Validation

**Automated Tests:** Run `test_sippy.py` to validate:

✅ Argument parsing and validation  
✅ Topology selection (TNF, TNA, SNO, all)  
✅ SNO feature gate restriction  
✅ Job scope filtering (main vs all)  
✅ Test scope filtering (feature vs all)  
✅ Output level selection (job vs test)  
✅ Network stack breakdown vs overall summary  

**Job-Level Validation:** ✅ Complete

✅ Sippy API queries return real job data from `/api/jobs`  
✅ Job filtering uses Topology variants (Topology:two-node-fencing, etc.)  
✅ Network stack breakdown uses NetworkStack variants (ipv4/ipv6/dual)  
✅ Pass rates and run counts display correctly  
✅ Overall GA readiness assessment (95% threshold)  

**Test-Level Validation:** ✅ Complete

✅ Sippy API queries return real test data from `/api/tests`  
✅ Feature gate filtering by test name labels ([OCPFeatureGate:DualReplica], etc.)  
✅ Network stack breakdown shows accurate per-stack statistics  
✅ Test-scope filtering returns only feature-gated tests  
✅ Individual test pass rates display correctly per network stack  
✅ Test-level output fully functional for all topologies

## Notes

- **Job-level is default**: Fast overview of job health across all topologies
- **Job-level for CI health**: Use `--output-level=job` to monitor overall job health (works for all topologies)
- **SNO usage**: Use `--test-scope=all` or `--output-level=job` for SNO queries
- Use for sprint planning and GA readiness assessment
- Complements TestGrid data with aggregated pass rates
- Helps identify which tests need attention
- Network stack breakdown helps identify IPv6-specific issues
