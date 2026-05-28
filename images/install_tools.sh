#!/usr/bin/bash

set -euo pipefail

GH_TOKEN_VER="${GH_TOKEN_VER:-2.0.8}"
GH_TOKEN_SHA="${GH_TOKEN_SHA:-867d9ebf7dd18e67e2599f0f890f3f41b8673e88c4394a32a05476024c41ea0f}"
GH_TOKEN_RETRY_COUNT="${GH_TOKEN_RETRY_COUNT:-3}"
GCLOUD_VERSION="${GCLOUD_VERSION:-567.0.0}"
GCLOUD_SHA256="${GCLOUD_SHA256:-bd5afc0d249609cb40d45f665209190fdd38b9937954291b8f9ae54206c75d83}"
GCLOUD_RETRY_COUNT="${GCLOUD_RETRY_COUNT:-3}"
MARKDOWNLINT_VERSION="${MARKDOWNLINT_VERSION:-0.40.0}"
MARKDOWNLINT_CLI2_VERSION="${MARKDOWNLINT_CLI2_VERSION:-0.22.1}"

retry_command() {
  local max_attempts="$1"
  shift
  local attempt=1

  until "$@"; do
    if [ "${attempt}" -ge "${max_attempts}" ]; then
      echo "Command failed after ${attempt} attempts: $*" >&2
      return 1
    fi
    echo "Attempt ${attempt}/${max_attempts} failed, retrying..." >&2
    sleep $((attempt * 2))
    attempt=$((attempt + 1))
  done
}

install_gh_token() {
  retry_command "${GH_TOKEN_RETRY_COUNT}" \
    curl -sSL --connect-timeout 10 --max-time 120 --fail \
      "https://github.com/Link-/gh-token/releases/download/v${GH_TOKEN_VER}/linux-amd64" \
      -o /usr/local/bin/gh-token

  echo "${GH_TOKEN_SHA}  /usr/local/bin/gh-token" | sha256sum -c -
  chmod +x /usr/local/bin/gh-token
}

install_google_cloud_cli() {
  local gcloud_tarball="google-cloud-cli-${GCLOUD_VERSION}-linux-x86_64.tar.gz"
  local gcloud_url="https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/${gcloud_tarball}"
  local gcloud_archive="/tmp/${gcloud_tarball}"

  retry_command "${GCLOUD_RETRY_COUNT}" \
    curl -sSL --connect-timeout 10 --max-time 300 --fail \
      "${gcloud_url}" \
      -o "${gcloud_archive}"

  echo "${GCLOUD_SHA256}  ${gcloud_archive}" | sha256sum -c -
  rm -rf /opt/google-cloud-sdk
  tar -xzf "${gcloud_archive}" -C /opt
  /opt/google-cloud-sdk/install.sh --quiet --path-update false
  for bin in gcloud gcloud-crc32c gsutil; do
    if [ ! -f "/usr/local/bin/${bin}" ]; then
      ln -sf "/opt/google-cloud-sdk/bin/${bin}" "/usr/local/bin/${bin}"
    fi
  done
  rm -f "${gcloud_archive}"
}

install_python_tools() {
    # Alias python to python3 if not already set for convenience
    if ! command -v python >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
        ln -sf "$(command -v python3)" /usr/local/bin/python
        echo "Created python symlink to python3."
    fi

    echo "Installing Python package dependencies..."
    python3 -m pip install \
        'uv==0.11.6' \
        'matplotlib==3.9.4'
    echo "Python package dependencies installed."
}

install_markdown_tools() {
  npm install -g \
    "markdownlint@v${MARKDOWNLINT_VERSION}" \
    "markdownlint-cli2@v${MARKDOWNLINT_CLI2_VERSION}" \
    markdownlint-cli2-formatter-json \
    markdownlint-cli2-formatter-pretty \
    markdownlint-cli2-formatter-junit
}

cleanup_install_artifacts() {
  dnf clean all
  npm cache clean --force || true
  rm -rf /var/cache/dnf /root/.npm/_cacache
}
