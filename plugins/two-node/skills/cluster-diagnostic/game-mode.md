# game

Argument starts with `game`. Interactive training mode. No SSH required.

1. Read the knowledge base.
2. Present mode selection via `AskUserQuestion`:
   - **quiz** — diagnose a broken cluster from pcs status output
   - **scenario** — choose the right action for an operational situation
   - **rapid-fire** — true/false on TNF facts, 10 questions

## quiz

Present a `pcs status` snippet with a problem. Ask the user to identify:
severity (BLOCKER/WARNING/INFO), root cause, and recovery action. Score
against the knowledge base. Progressive difficulty: INFO → WARNING → BLOCKER.

Scenarios (6 rounds):

1. STONITH failcount=4, resources Started
   → INFO. Historical timeout. `pcs resource cleanup`.

2. Corosync link status: disconnected to peer
   → WARNING. Cluster network down. Check NIC, switch, IP.

3. etcd DB size 7.2 GB / 8.6 GB quota, no alarm yet
   → WARNING. Approaching quota. Defrag each member individually.

4. One node OFFLINE, etcd shows "must force a new cluster"
   → BLOCKER. Network partition triggered fencing. Cluster will
   self-recover. Monitor `pcs status` and `etcdctl member list`.

5. Both nodes in standby, all resources Stopped, API unreachable
   → BLOCKER. Someone ran `pcs node standby --all`. SSH to node,
   `sudo pcs node unstandby --all`. Wait ~45 seconds.

6. Both nodes Online, both report `standalone_node` for themselves
   → BLOCKER. Split-brain after force-new-cluster. Pick winner by
   raft index, wipe loser's etcd data, clear CIB attributes,
   disable/enable etcd-clone.

## scenario

Present an operational situation. User picks from 3-4 options via
`AskUserQuestion`. Wrong answers explain why they're wrong using the
specific failure mode from the knowledge base. 5 rounds.

1. "You need to shut down the entire TNF cluster for datacenter
   maintenance. What do you do?"
   - a) `pcs node standby --all` then power off → WRONG: kills API
     immediately, standby persists across reboot
   - b) Cordon, drain, then `shutdown -h 1` on each node → WRONG:
     sequential shutdown triggers fencing race
   - c) Simultaneous Redfish GracefulShutdown on both nodes (Recommended)
     → CORRECT
   - d) Disable fencing, then shut down sequentially → WRONG: removes
     HA protection, not tested

2. "Node e920t-02 needs a kernel update. How do you take it down?"
   - a) `pcs node standby e920t-02` → WRONG: risky, not directly
     tested, etcd behavior uncertain
   - b) Cordon and drain, then reboot → WRONG: unnecessary for TNF,
     all nodes are control-plane
   - c) Redfish GracefulShutdown on e920t-02 only (Recommended)
     → CORRECT: clean Corosync departure, no fencing
   - d) `oc debug node/e920t-02 -- shutdown -h 1` → WRONG: 1-minute
     delay creates fencing window

3. "etcd is showing NOSPACE alarm. The cluster is read-only. What's
   your first step?"
   - a) Restart etcd → WRONG: won't help, DB is still full
   - b) Delete unnecessary data, then compact → CORRECT: etcd accepts
     deletes while read-only
   - c) Defrag immediately → WRONG: defrag without compaction won't
     reclaim space
   - d) Restore from backup → WRONG: not needed, recoverable in-place

4. "After a power outage, both nodes booted and you see `pcs status`
   shows both nodes Online with all resources Started. Is everything
   fine?"
   - a) Yes, the cluster recovered automatically → MAYBE: check
     `standalone_node` attribute — if both nodes claim it, you have
     a split-brain despite looking healthy
   - b) Check `crm_attribute --query --name standalone_node` on both
     nodes (Recommended) → CORRECT
   - c) Check cluster IDs for mismatch → WRONG: force-new-cluster
     preserves cluster IDs, so matching IDs don't rule out split-brain
   - d) Run `pcs resource cleanup` → WRONG: doesn't address the
     underlying split-brain

5. "The network between nodes went down. Fencing fired and one node
   rebooted. VIPs are now on the survivor. After the fenced node
   comes back, VIPs are still on the survivor. Is this a problem?"
   - a) Yes, move VIPs back to the original node → WRONG
   - b) No, this is correct non-preemptive Keepalived behavior
     (Recommended) → CORRECT
   - c) Restart Keepalived to trigger rebalance → WRONG: unnecessary
     and disruptive
   - d) Check if Keepalived is misconfigured → WRONG: this is by
     design

## rapid-fire

10 true/false statements. Present one at a time via `AskUserQuestion`.
Track correct/incorrect. At the end, show score and explain wrong answers.

1. "Sequential node shutdown is safe on TNF."
   → **False.** Triggers a Pacemaker fencing race.

2. "VIPs on the wrong node after recovery is a problem."
   → **False.** Non-preemptive Keepalived — correct HA behavior.

3. "`pcs node standby --all` is a safe way to shut down a TNF cluster."
   → **False.** Kills the API immediately. Standby persists across reboots.

4. "`force-new-cluster` creates a new etcd cluster ID."
   → **False.** It preserves the existing cluster ID.

5. "In a two-node cluster, a network partition causes quorum loss on
   both sides."
   → **False.** Two-node mode: each node retains quorum independently.

6. "After fencing, you should always run `pcs resource cleanup`."
   → **True.** Clears stale failed actions from the fencing cycle.

7. "The BMC/management network and cluster network can fail
   independently."
   → **True.** They're often on different VLANs/subnets.

8. "Redfish ForceOff on HPE iLO guarantees an instant power cut."
   → **False.** The OS may have ~4 seconds to send Corosync leave
   messages.

9. "Both nodes showing `standalone_node` for themselves is the
   reliable signal for split-brain."
   → **True.** Cluster ID comparison alone is insufficient.

10. "After a rolling restart, workload pods automatically rebalance
    across both nodes."
    → **False.** Pods stay on the survivor. Kubernetes does not
    preempt pods back.

## Scoring

- Quiz: 3 points per round (1 severity + 1 root cause + 1 recovery)
- Scenario: 1 point per correct choice
- Rapid-fire: 1 point per correct answer

End each game with:

```text
## Results
Score: X / Y
Rating: (Novice / Operator / Expert / TNF Master)

Novice: 0-40%
Operator: 41-70%
Expert: 71-90%
TNF Master: 91-100%
```
