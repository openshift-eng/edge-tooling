---
name: lvms-ci:generate-html-report
argument-hint: <release1,release2,...>
description: Generate an HTML report from existing LVMS CI analysis files
user-invocable: true
allowed-tools: Bash, Read, Glob, Grep
---

# lvms-ci:generate-html-report

## Synopsis
```bash
/lvms-ci:generate-html-report <release1,release2,...>
```

## Description
Generates an HTML report from existing analysis files in the work directory. This is useful for re-generating the report after analysis has already been completed by `/lvms-ci:doctor` or `/lvms-ci:analyze-release`.

## Arguments
- `$ARGUMENTS` (required): Comma-separated release versions (e.g., `4.20,4.21,4.22`)

## Scripts Directory
```bash
SHARED_SCRIPTS=plugins/shared/scripts
```

## Work Directory
```bash
WORKDIR=/tmp/lvms-ci-claude-workdir.$(date +%y%m%d)
```

## Steps

### Step 1: Run Finalize
```bash
bash ${SHARED_SCRIPTS}/doctor.sh finalize --product lvms --workdir ${WORKDIR} $ARGUMENTS
```

### Step 2: Report Completion
Display the path to the generated HTML file.

## Prerequisites
- Analysis files must already exist in `${WORKDIR}` (produced by `/lvms-ci:doctor` or `/lvms-ci:analyze-release`)
- Python 3
