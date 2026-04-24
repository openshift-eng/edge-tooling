---
name: lvms:setup-prereq
argument-hint: "[connected|disconnected]"
description: Set up prerequisites to test unreleased LVMS operator builds on OpenShift clusters
user-invocable: true
allowed-tools: Bash, Read, Write, Glob, Grep
---

# lvms:setup-prereq

## Synopsis

```bash
/lvms:setup-prereq [connected|disconnected]
```

**Examples:**
```bash
# Interactive (will prompt for deployment type)
/lvms:setup-prereq

# Connected cluster
/lvms:setup-prereq connected

# Disconnected cluster
/lvms:setup-prereq disconnected
```

## Description

Sets up prerequisites to test unreleased LVMS operator builds on OpenShift clusters. Supports two deployment scenarios:

1. **Connected cluster**: Using CatalogSource and ImageDigestMirrorSet (IDMS)
2. **Disconnected cluster**: Using oc-mirror to mirror images to a disconnected registry

## Required Information

Ask the user for:
1. **Deployment type**: Connected or Disconnected cluster
2. **Kubeconfig path**: Path to the kubeconfig file (default: `~/.kube/config`)
3. **If Connected**: Catalog image (e.g., `quay.io/redhat-user-workloads/logical-volume-manag-tenant/lvm-operator-catalog@sha256:...`)
4. **If Disconnected**:
   - OCP version (e.g., 4.17, 4.18, 4.19, 4.20)
   - Mirror registry URL (e.g., `registry.example.com:5000`)
   - Path to registry credentials file (default: `${XDG_RUNTIME_DIR}/containers/auth.json`)
   - Catalog image to mirror

To get the catalog image, insert the snapshot name in: `<konflux_prod_url>/ns/logical-volume-manag-tenant/applications/lvm-operator-catalog-<OCP_VERSION>/snapshots/<SNAPSHOT_NAME>`

## Implementation

### Common Validation (Both Flows)

1. Check kubeconfig: `ls -l <kubeconfig-path>`
2. Verify `oc` available: `which oc`
3. Test connectivity: `oc whoami --kubeconfig=<kubeconfig-path>`
4. Validate catalog image format (must contain `@sha256:` or `:tag`)
5. **Disconnected only**: Verify `oc-mirror`: `which oc-mirror`
6. **Disconnected only**: Check credentials file exists
7. **Disconnected only**: Inform user to ensure mirror registry credentials are in their auth file. Credentials can be retrieved via:
   ```bash
   oc get secret pull-secret -n openshift-config -o jsonpath='{.data.\.dockerconfigjson}' --kubeconfig=<kubeconfig-path> | base64 -d
   ```

---

## Flow A: Connected Cluster (IDMS + CatalogSource)

### Step 1: Clean Up Existing Resources
```bash
oc delete catalogsource qe-app-registry -n openshift-marketplace --kubeconfig=<kubeconfig-path> --ignore-not-found=true
oc get imagecontentsourcepolicy --kubeconfig=<kubeconfig-path>
# If any ICSP exists, warn user and delete:
oc delete imagecontentsourcepolicy --all --kubeconfig=<kubeconfig-path>
```

### Step 2: Create CatalogSource

Write `/tmp/lvms-catalogsource.yaml`:
```yaml
apiVersion: operators.coreos.com/v1alpha1
kind: CatalogSource
metadata:
  name: lvms-custom-catalog
  namespace: openshift-marketplace
spec:
  displayName: konflux
  publisher: OpenShift QE
  sourceType: grpc
  updateStrategy:
    registryPoll:
      interval: 15m
  image: <CATALOG_IMAGE>
```

### Step 3: Create ImageDigestMirrorSet

Write `/tmp/lvms-idms.yaml`:
```yaml
apiVersion: config.openshift.io/v1
kind: ImageDigestMirrorSet
metadata:
  name: lvm-operator-imagedigestmirrors
spec:
  imageDigestMirrors:
    - mirrors:
      - registry.stage.redhat.io/lvms4/lvms-operator-bundle
      - quay.io/redhat-user-workloads/logical-volume-manag-tenant/lvm-operator-bundle
      source: registry.redhat.io/lvms4/lvms-operator-bundle
    - mirrors:
      - registry.stage.redhat.io/lvms4/lvms-rhel9-operator
      - quay.io/redhat-user-workloads/logical-volume-manag-tenant/lvm-operator
      source: registry.redhat.io/lvms4/lvms-rhel9-operator
    - mirrors:
      - registry.stage.redhat.io/lvms4/lvms-must-gather-rhel9
      - quay.io/redhat-user-workloads/logical-volume-manag-tenant/lvms-must-gather
      source: registry.redhat.io/lvms4/lvms-must-gather-rhel9
    - mirrors:
      - quay.io/redhat-user-workloads/logical-volume-manag-tenant/lvm-operator-bundle
      source: registry.stage.redhat.io/lvms4/lvms-operator-bundle
    - mirrors:
      - quay.io/redhat-user-workloads/logical-volume-manag-tenant/lvm-operator
      source: registry.stage.redhat.io/lvms4/lvms-rhel9-operator
    - mirrors:
      - quay.io/redhat-user-workloads/logical-volume-manag-tenant/lvms-must-gather
      source: registry.stage.redhat.io/lvms4/lvms-must-gather-rhel9
```

### Step 4: Apply IDMS and Monitor MCP

Warn: "Applying IDMS will trigger node reboots. This may take 20-30 minutes."

```bash
oc apply -f /tmp/lvms-idms.yaml --kubeconfig=<kubeconfig-path>
oc wait mcp/master mcp/worker --for=condition=Updating --timeout=5m --kubeconfig=<kubeconfig-path>
oc get mcp --kubeconfig=<kubeconfig-path>
oc wait mcp/master mcp/worker --for=condition=Updated --for=condition=Updating=False --timeout=30m --kubeconfig=<kubeconfig-path>
oc get mcp --kubeconfig=<kubeconfig-path>
```

### Step 5: Apply CatalogSource

```bash
oc apply -f /tmp/lvms-catalogsource.yaml --kubeconfig=<kubeconfig-path>
oc wait catalogsource/lvms-custom-catalog -n openshift-marketplace --for=jsonpath='{.status.connectionState.lastObservedState}'=READY --timeout=5m --kubeconfig=<kubeconfig-path>
```

### Step 6: Verify

```bash
oc get catalogsource lvms-custom-catalog -n openshift-marketplace --kubeconfig=<kubeconfig-path>
oc get packagemanifest lvms-operator --kubeconfig=<kubeconfig-path>
```

Inform: "Prerequisites are set up. You can now install the unreleased LVMS operator from OperatorHub or create a Subscription."

---

## Flow B: Disconnected Cluster (oc-mirror)

**Note**: `oc-mirror` may not work reliably on macOS. Use Fedora or RHEL for best results.

### Step 1: Create ImageSetConfiguration

Write `/tmp/lvms-catalog-config.yaml`:
```yaml
kind: ImageSetConfiguration
apiVersion: mirror.openshift.io/v2alpha1
mirror:
  operators:
    - catalog: <CATALOG_IMAGE>
      packages:
        - name: lvms-operator
          channels:
            - name: 'stable-<OCP_VERSION>'
```

### Step 2: Run oc-mirror for Catalog

```bash
mkdir -p /tmp/oc-mirror-workspace
oc-mirror -c /tmp/lvms-catalog-config.yaml --workspace file:///tmp/oc-mirror-workspace docker://<MIRROR_REGISTRY> --v2 --dest-tls-verify=false
```

Check for errors: `ls -ltr /tmp/oc-mirror-workspace/working-dir/logs/mirroring_errors_*.txt`

### Step 3: Apply Generated CatalogSource

```bash
oc apply -f /tmp/oc-mirror-workspace/working-dir/cluster-resources/cs-*.yaml --kubeconfig=<kubeconfig-path>
oc wait catalogsource -n openshift-marketplace --all --for=jsonpath='{.status.connectionState.lastObservedState}'=READY --timeout=5m --kubeconfig=<kubeconfig-path>
```

### Step 4: Handle Mirroring Errors (if any)

If mirroring errors exist, extract failed images and create corrected references:
- `registry.redhat.io/lvms4/lvms-operator-bundle` -> `quay.io/redhat-user-workloads/logical-volume-manag-tenant/lvm-operator-bundle`
- `registry.redhat.io/lvms4/lvms-rhel9-operator` -> `quay.io/redhat-user-workloads/logical-volume-manag-tenant/lvm-operator`
- `registry.redhat.io/lvms4/lvms-must-gather-rhel9` -> `quay.io/redhat-user-workloads/logical-volume-manag-tenant/lvms-must-gather`

Write `/tmp/lvms-additional-images-config.yaml`:
```yaml
kind: ImageSetConfiguration
apiVersion: mirror.openshift.io/v2alpha1
mirror:
  additionalImages:
    - name: quay.io/redhat-user-workloads/logical-volume-manag-tenant/lvm-operator-bundle@sha256:<SHA_FROM_LOG>
    - name: quay.io/redhat-user-workloads/logical-volume-manag-tenant/lvm-operator@sha256:<SHA_FROM_LOG>
    - name: quay.io/redhat-user-workloads/logical-volume-manag-tenant/lvms-must-gather@sha256:<SHA_FROM_LOG>
```

### Step 5: Mirror Additional Images (if Step 4 executed)

```bash
oc-mirror -c /tmp/lvms-additional-images-config.yaml --workspace file:///tmp/oc-mirror-workspace docker://<MIRROR_REGISTRY> --v2 --dest-tls-verify=false
```

### Step 6: Create and Apply IDMS

Write `/tmp/lvms-idms.yaml` with mirrors pointing to `<MIRROR_REGISTRY>` (same structure as connected flow, with mirror registry as first mirror entry).

### Step 7: Apply IDMS and Monitor MCP

Same as connected flow Step 4.

### Step 8: Verify

```bash
oc get catalogsource -n openshift-marketplace --kubeconfig=<kubeconfig-path>
oc get packagemanifest lvms-operator --kubeconfig=<kubeconfig-path>
```

Inform: "Prerequisites are set up for disconnected cluster. You can now install the LVMS operator from OperatorHub or create a Subscription."

## Important Notes

- ImageDigestMirrorSet triggers MachineConfigPool updates and node reboots
- Deleting ImageContentSourcePolicy also triggers MCP updates
- MCP updates can take 20-30 minutes depending on cluster size
- User must have cluster-admin permissions
- Always wait for MCP to start updating before waiting for completion (avoids false positives)

## Error Handling

| Error | Action |
|-------|--------|
| Kubeconfig not found | Ask for correct path |
| Invalid catalog image format | Ask for correction |
| `oc` not available | Install OpenShift CLI |
| `oc whoami` fails | Verify kubeconfig and cluster accessibility |
| MCP doesn't start updating (5m) | Check `oc get mcp -o yaml` |
| MCP update times out | Provide manual check: `oc get mcp` and `oc get nodes` |
| `oc-mirror` not available | Install from OpenShift mirror downloads |
| Registry credentials missing | Ask for path or create via `podman login` |
| oc-mirror auth errors | Verify credentials file and registry connectivity |
| oc-mirror mirroring fails | Check disk space and connectivity |
| CatalogSource fails to apply | Verify mirrored catalog image exists in mirror registry |
