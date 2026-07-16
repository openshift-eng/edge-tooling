// Process govulncheck JSON output and publish the result for edge-cve
// collection. Run inside the scan container (OpenShift Job or local podman)
// after govulncheck completes.
//
// Two output modes, selected by the RESULT_DIR env var:
//   - RESULT_DIR set (local/podman mode): write result.json (curated) and a
//     full, uncapped copy of the raw govulncheck.json under
//     RESULT_DIR/<target-id>/ - local disk isn't size-constrained.
//   - RESULT_DIR unset (OpenShift Job mode): publish only the curated
//     result.json as a labeled ConfigMap via the in-cluster Kubernetes API
//     (raw REST calls, since the job image only ships the Go toolchain, not
//     kubectl/oc). The raw govulncheck output is NOT stored in the
//     ConfigMap - it's typically far too large relative to the ~1MiB
//     ConfigMap size limit to be useful there; matched_findings already
//     carries the CVE-relevant subset.
package main

import (
	"bufio"
	"bytes"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

const serviceAccountDir = "/var/run/secrets/kubernetes.io/serviceaccount"

func main() {
	targetID := os.Getenv("TARGET_ID")
	cveSet := parseCSVUpper(os.Getenv("CVE_IDS"))
	ticketKeys := parseCSV(os.Getenv("TICKET_KEYS"))
	scanExit, _ := strconv.Atoi(os.Getenv("SCAN_EXIT"))

	findings := readNDJSON("/tmp/govulncheck.json")
	matched := matchFindings(findings, cveSet)

	// A shell exit code > 128 means the process was terminated by a signal
	// (e.g. 137 = 128+SIGKILL, typically an OOM kill). govulncheck's own
	// exit codes (0 = clean, 3 = vulnerabilities found, 1 = error) are all
	// < 128, so this never misclassifies a real result. In this case
	// /tmp/govulncheck.json is partial/empty, so "no matches" does NOT mean
	// "not affected" - it means the scan never finished.
	scanIncomplete := scanExit > 128

	result := map[string]any{
		"target_id":        targetID,
		"repo_url":         os.Getenv("REPO_URL"),
		"repo_slug":        os.Getenv("REPO_SLUG"),
		"git_ref":          os.Getenv("GIT_REF"),
		"commit":           os.Getenv("COMMIT"),
		"cve_ids":          mapKeys(cveSet),
		"ticket_keys":      ticketKeys,
		"scan_exit_code":   scanExit,
		"scan_incomplete":  scanIncomplete,
		"affected":         len(matched) > 0,
		"matched_findings": matched,
		"finding_count":    len(findings),
		"stderr_tail":      tailFile("/tmp/govulncheck.err", 8000),
	}

	resultJSON, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		fmt.Fprintf(os.Stderr, "failed to marshal result: %v\n", err)
		os.Exit(1)
	}

	// Always surface the result in the pod/container log, since publishing
	// may fail independently of the scan itself. Bracketed with markers so
	// callers that capture container stdout directly (e.g.
	// run_single_repo_scan.sh, which can't rely on a bind-mounted RESULT_DIR
	// write being immediately visible on the host after the container exits)
	// can reliably extract just the JSON amid toolchain/git log noise.
	fmt.Println("EDGE_CVE_RESULT_JSON_BEGIN")
	fmt.Println(string(resultJSON))
	fmt.Println("EDGE_CVE_RESULT_JSON_END")

	if resultDir := os.Getenv("RESULT_DIR"); resultDir != "" {
		if err := writeLocalResult(resultDir, targetID, resultJSON); err != nil {
			fmt.Fprintf(os.Stderr, "failed to write local result: %v\n", err)
			os.Exit(1)
		}
		return
	}

	if err := publishConfigMap(targetID, os.Getenv("REPO_LABEL"), resultJSON); err != nil {
		fmt.Fprintf(os.Stderr, "failed to publish result configmap: %v\n", err)
		os.Exit(1)
	}
}

// writeLocalResult writes the curated result.json plus a full, uncapped copy
// of the raw govulncheck.json output to RESULT_DIR/<sanitized-target-id>/,
// for local (non-cluster) runs. Unlike the ConfigMap path, local disk has no
// meaningful size constraint, so the raw output isn't truncated here.
func writeLocalResult(resultDir, targetID string, resultJSON []byte) error {
	dir := filepath.Join(resultDir, sanitizeLabel(targetID))
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(dir, "result.json"), resultJSON, 0o644); err != nil {
		return err
	}
	rawData, err := os.ReadFile("/tmp/govulncheck.json")
	if err != nil {
		// No raw output to copy (e.g. govulncheck never produced any) - not fatal.
		return nil
	}
	return os.WriteFile(filepath.Join(dir, "govulncheck.json"), rawData, 0o644)
}

func publishConfigMap(targetID, repoLabel string, resultJSON []byte) error {
	client, apiServer, token, namespace, err := inClusterClient()
	if err != nil {
		return err
	}

	name := configMapName(targetID)
	body := map[string]any{
		"apiVersion": "v1",
		"kind":       "ConfigMap",
		"metadata": map[string]any{
			"name":      name,
			"namespace": namespace,
			"labels": map[string]string{
				"app.kubernetes.io/name": "edge-cve-govulncheck-result",
				"edge-cve/target-id":     sanitizeLabel(targetID),
				"edge-cve/repo":          sanitizeLabel(repoLabel),
			},
		},
		"data": map[string]string{
			"result.json": string(resultJSON),
		},
	}
	payload, err := json.Marshal(body)
	if err != nil {
		return err
	}

	createURL := fmt.Sprintf("%s/api/v1/namespaces/%s/configmaps", apiServer, namespace)
	resp, err := doRequest(client, http.MethodPost, createURL, token, "application/json", payload)
	if err != nil {
		return err
	}
	if resp.status == http.StatusCreated {
		return nil
	}
	if resp.status != http.StatusConflict {
		return fmt.Errorf("create configmap %s failed: %d %s", name, resp.status, resp.body)
	}

	// Already exists (e.g. job retry) - merge-patch the data/labels in place.
	patch := map[string]any{
		"metadata": map[string]any{"labels": body["metadata"].(map[string]any)["labels"]},
		"data":     body["data"],
	}
	patchPayload, err := json.Marshal(patch)
	if err != nil {
		return err
	}
	patchURL := fmt.Sprintf("%s/api/v1/namespaces/%s/configmaps/%s", apiServer, namespace, name)
	resp, err = doRequest(client, http.MethodPatch, patchURL, token, "application/merge-patch+json", patchPayload)
	if err != nil {
		return err
	}
	if resp.status != http.StatusOK {
		return fmt.Errorf("patch configmap %s failed: %d %s", name, resp.status, resp.body)
	}
	return nil
}

type httpResult struct {
	status int
	body   string
}

func doRequest(client *http.Client, method, url, token, contentType string, payload []byte) (httpResult, error) {
	req, err := http.NewRequest(method, url, bytes.NewReader(payload))
	if err != nil {
		return httpResult{}, err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", contentType)
	req.Header.Set("Accept", "application/json")

	resp, err := client.Do(req)
	if err != nil {
		return httpResult{}, err
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)
	return httpResult{status: resp.StatusCode, body: string(respBody)}, nil
}

func inClusterClient() (*http.Client, string, string, string, error) {
	host := os.Getenv("KUBERNETES_SERVICE_HOST")
	port := os.Getenv("KUBERNETES_SERVICE_PORT")
	if host == "" || port == "" {
		return nil, "", "", "", fmt.Errorf("KUBERNETES_SERVICE_HOST/PORT not set; not running in-cluster")
	}

	tokenBytes, err := os.ReadFile(serviceAccountDir + "/token")
	if err != nil {
		return nil, "", "", "", fmt.Errorf("reading service account token: %w", err)
	}
	nsBytes, err := os.ReadFile(serviceAccountDir + "/namespace")
	if err != nil {
		return nil, "", "", "", fmt.Errorf("reading service account namespace: %w", err)
	}
	caBytes, err := os.ReadFile(serviceAccountDir + "/ca.crt")
	if err != nil {
		return nil, "", "", "", fmt.Errorf("reading service account ca.crt: %w", err)
	}

	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(caBytes) {
		return nil, "", "", "", fmt.Errorf("failed to parse ca.crt")
	}

	client := &http.Client{
		Transport: &http.Transport{
			TLSClientConfig: &tls.Config{RootCAs: pool},
		},
	}
	apiServer := fmt.Sprintf("https://%s:%s", host, port)
	return client, apiServer, strings.TrimSpace(string(tokenBytes)), strings.TrimSpace(string(nsBytes)), nil
}

func configMapName(targetID string) string {
	name := "govulncheck-result-" + sanitizeLabel(targetID)
	if len(name) > 253 {
		name = name[:253]
	}
	return strings.Trim(name, "-.")
}

func sanitizeLabel(raw string) string {
	var b strings.Builder
	for _, r := range raw {
		switch {
		case r >= 'a' && r <= 'z', r >= '0' && r <= '9', r == '-', r == '_', r == '.':
			b.WriteRune(r)
		case r >= 'A' && r <= 'Z':
			b.WriteRune(r + ('a' - 'A'))
		default:
			b.WriteRune('-')
		}
	}
	out := strings.Trim(b.String(), "-_.")
	if len(out) > 63 {
		out = out[:63]
	}
	return out
}

func parseCSV(raw string) []string {
	var out []string
	for _, part := range strings.Split(raw, ",") {
		part = strings.TrimSpace(part)
		if part != "" {
			out = append(out, part)
		}
	}
	return out
}

func parseCSVUpper(raw string) map[string]bool {
	set := make(map[string]bool)
	for _, part := range parseCSV(raw) {
		set[strings.ToUpper(part)] = true
	}
	return set
}

func mapKeys(set map[string]bool) []string {
	out := make([]string, 0, len(set))
	for key := range set {
		out = append(out, key)
	}
	return out
}

func readNDJSON(path string) []map[string]any {
	file, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer file.Close()

	var entries []map[string]any
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var entry map[string]any
		if json.Unmarshal([]byte(line), &entry) == nil {
			entries = append(entries, entry)
		}
	}
	return entries
}

// matchFindings selects the findings relevant to this scan. If CVE_IDS was
// provided (the Jira-driven bulk workflow, where we're checking a repo
// against specific known CVEs), only findings matching one of those IDs
// count. If no CVE_IDS was given (ad-hoc "is this repo/ref affected by
// anything" checks - see run_single_repo_scan.sh), every vulnerability
// govulncheck reports counts, since there's no specific CVE to filter to.
func matchFindings(findings []map[string]any, cveSet map[string]bool) []map[string]any {
	matchAny := len(cveSet) == 0
	var matched []map[string]any
	for _, entry := range findings {
		if matchAny {
			if findingOSV(entry) != nil {
				matched = append(matched, entry)
			}
			continue
		}
		if entryMatchesCVE(entry, cveSet) {
			matched = append(matched, entry)
		}
	}
	return matched
}

func findingOSV(entry map[string]any) map[string]any {
	finding, ok := entry["finding"].(map[string]any)
	if !ok {
		finding = entry
	}
	osv, _ := finding["osv"].(map[string]any)
	if osv == nil {
		osv, _ = finding["vulnerability"].(map[string]any)
	}
	return osv
}

func entryMatchesCVE(entry map[string]any, cveSet map[string]bool) bool {
	osv := findingOSV(entry)
	if osv == nil {
		return false
	}
	if id, ok := osv["id"].(string); ok && cveSet[strings.ToUpper(id)] {
		return true
	}
	aliases, _ := osv["aliases"].([]any)
	for _, alias := range aliases {
		if s, ok := alias.(string); ok && cveSet[strings.ToUpper(s)] {
			return true
		}
	}
	return false
}

func tailFile(path string, max int) string {
	content, _ := readCappedTail(path, max)
	return content
}

// readCappedTail returns the file contents, keeping only the last `max`
// bytes if the file is larger. The second return value reports whether
// truncation occurred.
func readCappedTail(path string, max int) (string, bool) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", false
	}
	if len(data) <= max {
		return string(data), false
	}
	return string(data[len(data)-max:]), true
}
