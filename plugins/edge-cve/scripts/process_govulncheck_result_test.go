package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestReadNDJSONValidSkipsBlankLines(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "out.json")
	content := "{\"osv\":{\"id\":\"GO-1\"}}\n\n{\"finding\":{\"osv\":\"GO-1\"}}\n"
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	entries, err := readNDJSON(path)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(entries) != 2 {
		t.Fatalf("expected 2 entries, got %d", len(entries))
	}
}

func TestReadNDJSONOpenFailure(t *testing.T) {
	_, err := readNDJSON(filepath.Join(t.TempDir(), "missing.json"))
	if err == nil || !strings.Contains(err.Error(), "open ") {
		t.Fatalf("expected open error, got %v", err)
	}
}

func TestReadNDJSONDecodeFailure(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "bad.json")
	if err := os.WriteFile(path, []byte("{\"ok\":true}\nnot-json\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	_, err := readNDJSON(path)
	if err == nil || !strings.Contains(err.Error(), "decode NDJSON") {
		t.Fatalf("expected decode error, got %v", err)
	}
}

func TestParseScanExitValid(t *testing.T) {
	got, err := parseScanExit("137")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != 137 {
		t.Fatalf("expected 137, got %d", got)
	}
}

func TestParseScanExitMissing(t *testing.T) {
	_, err := parseScanExit("")
	if err == nil || !strings.Contains(err.Error(), "required") {
		t.Fatalf("expected required error, got %v", err)
	}
}

func TestParseScanExitMalformed(t *testing.T) {
	_, err := parseScanExit("not-a-number")
	if err == nil || !strings.Contains(err.Error(), "invalid SCAN_EXIT") {
		t.Fatalf("expected invalid SCAN_EXIT error, got %v", err)
	}
}

func TestMatchFindingsStringOSVID(t *testing.T) {
	entries := []map[string]any{
		{
			"osv": map[string]any{
				"id":      "GO-2024-1234",
				"aliases": []any{"CVE-2024-99999"},
			},
		},
		{
			"finding": map[string]any{
				"osv":   "GO-2024-1234",
				"trace": []any{map[string]any{"module": "example.com/mod"}},
			},
		},
		{
			"progress": map[string]any{"message": "scanning"},
		},
	}

	// CVE filter matches via alias resolved from top-level OSV catalog.
	matched := matchFindings(entries, map[string]bool{"CVE-2024-99999": true})
	if len(matched) != 1 {
		t.Fatalf("expected 1 matched finding, got %d", len(matched))
	}
	if _, ok := matched[0]["finding"]; !ok {
		t.Fatalf("matched entry should preserve original finding record: %#v", matched[0])
	}
	if matched[0]["finding"].(map[string]any)["osv"] != "GO-2024-1234" {
		t.Fatalf("finding.osv string id should be preserved, got %#v", matched[0]["finding"])
	}
}

func TestMatchFindingsEmbeddedOSVObject(t *testing.T) {
	entries := []map[string]any{
		{
			"finding": map[string]any{
				"osv": map[string]any{
					"id":      "GO-2023-1",
					"aliases": []any{"CVE-2023-11111"},
				},
			},
		},
	}
	matched := matchFindings(entries, map[string]bool{"CVE-2023-11111": true})
	if len(matched) != 1 {
		t.Fatalf("expected embedded osv object to match alias, got %d", len(matched))
	}
}

func TestMatchFindingsMatchAnySkipsCatalogOnly(t *testing.T) {
	entries := []map[string]any{
		{"osv": map[string]any{"id": "GO-2024-1", "aliases": []any{"CVE-1"}}},
		{"finding": map[string]any{"osv": "GO-2024-1"}},
	}
	matched := matchFindings(entries, nil)
	if len(matched) != 1 {
		t.Fatalf("matchAny should return finding entries only, got %d", len(matched))
	}
	if _, ok := matched[0]["finding"]; !ok {
		t.Fatalf("expected finding entry, got %#v", matched[0])
	}
}

func TestMatchFindingsNoMatch(t *testing.T) {
	entries := []map[string]any{
		{"osv": map[string]any{"id": "GO-2024-1", "aliases": []any{"CVE-1"}}},
		{"finding": map[string]any{"osv": "GO-2024-1"}},
	}
	matched := matchFindings(entries, map[string]bool{"CVE-9999": true})
	if len(matched) != 0 {
		t.Fatalf("expected no matches, got %d", len(matched))
	}
}
