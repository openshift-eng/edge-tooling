"""Red Hat Catalog (Pyxis) API client for checking published MicroShift versions."""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import requests

try:
    from requests_gssapi import HTTPSPNEGOAuth
    _KERBEROS_AUTH = HTTPSPNEGOAuth()
except ImportError:
    _KERBEROS_AUTH = None

PYXIS_BASE_URL = "https://catalog.redhat.com/api/containers/v1"
PYXIS_STAGE_BASE_URL = "https://pyxis.stage.engineering.redhat.com/v1"
CONTAINERFILE_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/openshift/microshift"
    "/{commit}/packaging/images/bootc/Containerfile"
)

_BOOTC_REPO_TEMPLATE = {
    "prod": (
        PYXIS_BASE_URL + "/repositories/registry/registry.access.redhat.com"
        "/repository/openshift4/microshift-bootc-rhel{rhel}/images"
    ),
    "stage": (
        PYXIS_STAGE_BASE_URL + "/repositories/registry/registry.access.redhat.com"
        "/repository/openshift4/microshift-bootc-rhel{rhel}/images"
    ),
}

_GRAPHQL_URLS = {
    "prod": "https://catalog.redhat.com/api/containers/graphql/",
    "stage": "https://catalog.stage.redhat.com/api/containers/graphql/",
}

_REPO_BASE_URLS = {
    "prod": PYXIS_BASE_URL,
    "stage": PYXIS_STAGE_BASE_URL,
}

_BOOTC_REPO_NAME = "openshift{major}/microshift-bootc-rhel{rhel}"

_GRAPHQL_IMAGES_QUERY = """
query GET_REPOSITORY_BY_ID_IMAGES_HISTORY(
  $id: ObjectIDFilterScalar
  $page: Int! = 0
  $page_size: Int!
  $filter: ContainerImageFilter
  $sort_by: [SortBy]
) {
  ContainerRepository: get_repository(id: $id) {
    data {
      registry
      repository
      edges {
        images(page: $page, page_size: $page_size, filter: $filter, sort_by: $sort_by) {
          total
          data {
            _id
            image_id
            docker_image_digest
            architecture
            parsed_data {
              labels {
                name
                value
              }
              env_variables
            }
            freshness_grades {
              grade
            }
            container_grades {
              status
              status_message
            }
            repositories {
              repository
              push_date
              manifest_schema2_digest
              tags {
                name
              }
            }
          }
        }
      }
    }
  }
}
"""

# Cache repo IDs to avoid repeated lookups within a run
_repo_id_cache = {}

_LABEL_COMMIT_ID = "io.openshift.build.commit.id"
_LABEL_COMMIT_URL = "io.openshift.build.commit.url"
_LABEL_SOURCE_LOCATION = "io.openshift.build.source-location"

logger = logging.getLogger(__name__)


def _catalog_auth(catalog):
    """Return auth object for Pyxis requests. Stage requires Kerberos."""
    if catalog == "stage":
        if _KERBEROS_AUTH is None:
            logger.warning("requests-gssapi not installed — stage Pyxis "
                           "requires Kerberos (pip install requests-gssapi)")
        return _KERBEROS_AUTH
    return None


def _catalog_url(catalog="prod", rhel=9):
    """Return the Pyxis images URL for the given catalog and RHEL version.

    Args:
        catalog: "prod" or "stage".
        rhel: RHEL version (9 or 10). Default 9 for backward compatibility.

    Returns:
        str: Full API URL for the bootc images endpoint.
    """
    template = _BOOTC_REPO_TEMPLATE.get(catalog, _BOOTC_REPO_TEMPLATE["prod"])
    return template.format(rhel=rhel)


def _fetch_page(page, catalog="prod"):
    """Fetch a single page of bootc images from Pyxis.

    Args:
        page: Page number (0-indexed).
        catalog: "prod" or "stage".

    Returns:
        str: Response text.
    """
    url = _catalog_url(catalog)
    params = {
        "filter": "architecture==amd64",
        "page_size": 100,
        "page": page,
    }
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.text


def _parse_env_variables(env_list):
    """Parse environment variables from Pyxis parsed_data.env_variables.

    Args:
        env_list: List of "KEY=VALUE" strings.

    Returns:
        dict: Mapping of env var names to values.
    """
    result = {}
    for entry in env_list or []:
        if "=" in entry:
            key, _, value = entry.partition("=")
            result[key] = value
    return result


def _parse_labels(labels_list):
    """Parse labels from Pyxis parsed_data.labels.

    Args:
        labels_list: List of {name, value} dicts.

    Returns:
        dict: Mapping of label names to values.
    """
    return {lbl["name"]: lbl["value"]
            for lbl in labels_list or []
            if "name" in lbl and "value" in lbl}


def _parse_image_metadata(image):
    """Extract structured metadata from a single Pyxis image dict.

    Args:
        image: A single image dict from the Pyxis API data[] array.

    Returns:
        dict with keys: image_id, commit_id, commit_short, commit_url,
            source_url, source_git_tag, containerfile_url, tags,
            version_tags, assembly_version, last_update_date.
    """
    parsed = image.get("parsed_data") or {}
    labels = _parse_labels(parsed.get("labels"))
    env = _parse_env_variables(parsed.get("env_variables"))

    commit_id = (labels.get(_LABEL_COMMIT_ID)
                 or env.get("SOURCE_GIT_COMMIT"))
    commit_short = env.get("OS_GIT_COMMIT")
    commit_url = labels.get(_LABEL_COMMIT_URL)
    source_url = (labels.get(_LABEL_SOURCE_LOCATION)
                  or env.get("SOURCE_GIT_URL"))
    source_git_tag = env.get("SOURCE_GIT_TAG")

    containerfile_url = None
    if commit_id:
        containerfile_url = CONTAINERFILE_URL_TEMPLATE.format(commit=commit_id)

    repos = image.get("repositories") or []
    raw_tags = repos[0].get("tags", []) if repos else []
    tags = [{"name": t["name"], "added_date": t.get("added_date")}
            for t in raw_tags]

    version_pattern = re.compile(r"^v?\d+\.\d+(\.\d+)?$")
    version_tags = [t["name"] for t in raw_tags
                    if version_pattern.match(t["name"])]

    assembly_match = re.search(
        r"assembly\.(\d+\.\d+\.\d+)", " ".join(t["name"] for t in raw_tags)
    )
    assembly_version = assembly_match.group(1) if assembly_match else None

    freshness = image.get("freshness_grades") or []
    freshness_grade = freshness[-1].get("grade") if freshness else None
    container_grades = image.get("container_grades") or {}

    return {
        "image_id": image.get("_id"),
        "commit_id": commit_id,
        "commit_short": commit_short,
        "commit_url": commit_url,
        "source_url": source_url,
        "source_git_tag": source_git_tag,
        "containerfile_url": containerfile_url,
        "tags": tags,
        "version_tags": version_tags,
        "assembly_version": assembly_version,
        "last_update_date": image.get("last_update_date"),
        "freshness_grade": freshness_grade,
        "container_grade_status": container_grades.get("status"),
    }


def _scan_pages_for_versions(minor_version, pages=5):
    """Scan Pyxis pages for all published z-stream versions of a minor version.

    Args:
        minor_version: e.g., "4.21".
        pages: Number of pages to scan.

    Returns:
        set[int]: Set of z-stream numbers found (e.g., {0, 1, 7, 13}).
    """
    pattern = re.compile(rf"assembly\.{re.escape(minor_version)}\.(\d+)")
    found_z = set()

    with ThreadPoolExecutor(max_workers=pages) as executor:
        futures = [executor.submit(_fetch_page, p) for p in range(pages)]
        for future in futures:
            try:
                text = future.result()
                for match in pattern.finditer(text):
                    found_z.add(int(match.group(1)))
            except requests.RequestException as e:
                logger.warning("Pyxis page fetch failed: %s", e)

    return found_z


def is_version_published(version, pages=5):
    """Check if MicroShift version X.Y.Z has been published.

    Checks the Pyxis bootc catalog first (4.17+), then falls back to
    the Hydra errata API for older versions (4.14-4.16) that were
    shipped as RPMs only.

    Args:
        version: Full version string, e.g., "4.21.7".
        pages: Number of pages to paginate (page_size=100).

    Returns:
        bool: True if the version has been published.
    """
    pattern = re.compile(rf"\bassembly\.{re.escape(version)}\b")

    with ThreadPoolExecutor(max_workers=pages) as executor:
        futures = [executor.submit(_fetch_page, p) for p in range(pages)]
        for future in futures:
            try:
                text = future.result()
                if pattern.search(text):
                    return True
            except requests.RequestException as e:
                logger.warning("Pyxis page fetch failed: %s", e)

    # Fallback: check errata for pre-bootc versions.
    # Uses target_z <= latest_z as a heuristic. Some z-streams are
    # skipped (e.g., 4.20.7), so this can produce false positives for
    # skipped versions — safe because it only triggers ALREADY RELEASED.
    minor = ".".join(version.split(".")[:2])
    errata = _find_latest_from_errata(minor)
    if errata:
        try:
            target_z = int(version.split(".")[2])
            return target_z <= errata["z"]
        except (IndexError, ValueError) as e:
            logger.warning("Failed to parse version '%s' for errata "
                           "comparison: %s", version, e)

    return False


def find_latest_published_zstream(minor_version, pages=5):
    """Find the highest published z-stream for a minor version.

    Args:
        minor_version: e.g., "4.21".
        pages: Number of pages to scan.

    Returns:
        dict or None: {"version": "4.21.13", "z": 13} or None if not found.
    """
    found_z = _scan_pages_for_versions(minor_version, pages)
    if not found_z:
        return None

    highest_z = max(found_z)
    return {
        "version": f"{minor_version}.{highest_z}",
        "z": highest_z,
    }


def get_publish_date(version, pages=5):
    """Get the publish date for a version from Pyxis image metadata.

    Searches for an image with the assembly.X.Y.Z tag and returns the
    tag's added_date or the image's last_update_date as a fallback.

    This is used when git tags are unavailable (e.g., ART hasn't pushed
    them yet) but Pyxis confirms the version is published.

    Args:
        version: Full version string, e.g., "4.21.11".
        pages: Number of pages to scan.

    Returns:
        str or None: Date in YYYY-MM-DD format, or None if not found.
    """
    # Assembly tags in Pyxis appear as substrings in longer tag names, e.g.:
    # "v4.21-202604201054.p2.g7f7539e.assembly.4.21.11.el9"
    assembly_pattern = re.compile(rf"\bassembly\.{re.escape(version)}\b")

    for page in range(pages):
        try:
            text = _fetch_page(page)
            data = json.loads(text)
            for image in data.get("data", []):
                repos = image.get("repositories", [])
                for repo in repos:
                    tags = repo.get("tags", [])
                    for tag in tags:
                        if assembly_pattern.search(tag.get("name", "")):
                            date_str = (
                                tag.get("added_date")
                                or image.get("last_update_date")
                                or ""
                            )
                            if date_str:
                                return date_str[:10]  # YYYY-MM-DD
        except (requests.RequestException, json.JSONDecodeError) as e:
            logger.warning("Pyxis date lookup failed on page %d: %s", page, e)

    return None


def _find_latest_from_errata(minor_version):
    """Find the latest published MicroShift version from Red Hat errata.

    Queries the public Hydra search API for published MicroShift errata
    advisories. This covers all versions including those that predate the
    bootc container image (4.14, 4.15, 4.16).

    Args:
        minor_version: e.g., "4.16".

    Returns:
        dict or None: {"version": "4.16.58", "z": 58, "date": "2026-03-19"}
            or None if not found.
    """
    url = "https://access.redhat.com/hydra/rest/search/kcs"
    params = {
        "q": (f'"Red Hat build of MicroShift {minor_version}"'
              " documentKind:Errata"),
        "start": 0,
        "rows": 10,
        "sort": "portal_publication_date desc",
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, json.JSONDecodeError) as e:
        logger.warning("Hydra errata search failed for %s: %s",
                       minor_version, e)
        return None

    pattern = re.compile(
        rf"\b{re.escape(minor_version)}\.(\d+)\b"
    )
    for doc in data.get("response", {}).get("docs", []):
        synopsis = doc.get("portal_synopsis", "")
        match = pattern.search(synopsis)
        if match:
            z = int(match.group(1))
            date = doc.get("portal_publication_date", "")[:10]
            return {
                "version": f"{minor_version}.{z}",
                "z": z,
                "date": date,
            }

    return None


def find_latest_published_zstream_any(minor_version, pages=5):
    """Find the latest published z-stream, checking Pyxis then errata.

    Tries the Pyxis bootc catalog first (fast, works for 4.17+), then
    falls back to the Hydra errata API (covers all versions including
    pre-bootc 4.14-4.16).

    Args:
        minor_version: e.g., "4.21" or "4.16".
        pages: Number of Pyxis pages to scan.

    Returns:
        dict or None: {"version": "4.16.58", "z": 58, "date": "..."} or None.
            The "date" key is present when sourced from errata.
    """
    result = find_latest_published_zstream(minor_version, pages)
    if result:
        return result

    logger.info("No bootc images for %s, checking errata...",
                minor_version)
    errata = _find_latest_from_errata(minor_version)
    if errata:
        logger.info("Found %s from errata (published %s)",
                    errata["version"], errata.get("date", "?"))
        return {
            "version": errata["version"],
            "z": errata["z"],
            "date": errata.get("date"),
        }

    return None


def extract_commit_from_image(version, pages=5):
    """Extract the git commit hash from Pyxis image tags for a version.

    Pyxis image tags embed the source commit as a short hash prefixed with 'g',
    e.g., v4.21-202605181229.p2.g919341c.assembly.4.21.16.el9
    contains commit 919341c.

    Args:
        version: e.g., "4.21.16".
        pages: Number of pages to scan.

    Returns:
        str or None: Short git commit hash, or None if not found.
    """
    assembly_pattern = re.compile(rf"\bassembly\.{re.escape(version)}\b")
    commit_pattern = re.compile(r"\.g([0-9a-f]{7,})\.")

    for page in range(pages):
        try:
            text = _fetch_page(page)
            data = json.loads(text)
            for image in data.get("data", []):
                for repo in image.get("repositories", []):
                    for tag in repo.get("tags", []):
                        name = tag.get("name", "")
                        if assembly_pattern.search(name):
                            match = commit_pattern.search(name)
                            if match:
                                return match.group(1)
        except (requests.RequestException, json.JSONDecodeError) as e:
            logger.warning("Pyxis commit lookup failed on page %d: %s",
                           page, e)

    return None


def find_all_published_versions(minor_version, pages=5):
    """Find all published z-stream versions for a minor version.

    Args:
        minor_version: e.g., "4.21".
        pages: Number of pages to scan.

    Returns:
        list[str]: Sorted list of published versions, e.g., ["4.21.0", "4.21.1", ...].
    """
    found_z = _scan_pages_for_versions(minor_version, pages)
    return [f"{minor_version}.{z}" for z in sorted(found_z)]


def fetch_all_bootc_images(catalog="prod", pages=5):
    """Fetch all amd64 bootc images from the catalog with metadata.

    Args:
        catalog: "prod" or "stage".
        pages: Number of pages to fetch (page_size=100).

    Returns:
        list[dict]: One dict per image with keys: image_id, commit_id,
            commit_short, commit_url, source_url, source_git_tag,
            containerfile_url, tags, version_tags, assembly_version,
            last_update_date.
    """
    images = []
    with ThreadPoolExecutor(max_workers=pages) as executor:
        futures = [executor.submit(_fetch_page, p, catalog)
                   for p in range(pages)]
        for future in futures:
            try:
                text = future.result()
                data = json.loads(text)
                for image in data.get("data", []):
                    images.append(_parse_image_metadata(image))
            except requests.HTTPError as e:
                if catalog == "stage" and e.response.status_code == 403:
                    logger.warning("Stage catalog unreachable (403), "
                                   "falling back to prod")
                    return fetch_all_bootc_images(catalog="prod", pages=pages)
                logger.warning("Failed to fetch bootc images: %s", e)
            except (requests.RequestException, json.JSONDecodeError) as e:
                logger.warning("Failed to fetch/parse bootc images: %s", e)

    images.sort(key=lambda img: img.get("assembly_version") or "")
    return images


def check_catalog_image(version, catalog="prod", arch="amd64", rhel=9):
    """Check if a specific version's bootc image exists in the catalog.

    Args:
        version: Full version string, e.g., "4.21.8".
        catalog: "prod" or "stage".
        arch: Architecture to query, e.g., "amd64" or "arm64".
        rhel: RHEL version (9 or 10).

    Returns:
        dict: {valid: bool, reason: str, image: dict | None, catalog: str}
    """
    tag = re.sub(r"-(ec|rc)\.\d+$", "", version)
    assembly_pattern = re.compile(rf"\bassembly\.{re.escape(tag)}\b")

    url = _catalog_url(catalog, rhel=rhel)
    for page in range(5):
        params = {
            "filter": f"architecture=={arch}",
            "page_size": 100,
            "page": page,
        }
        try:
            resp = requests.get(url, params=params, timeout=30,
                                auth=_catalog_auth(catalog),
                                verify=(catalog != "stage"))
            resp.raise_for_status()
            data = resp.json()
        except (requests.HTTPError, requests.RequestException,
                json.JSONDecodeError) as e:
            return {"valid": False,
                    "reason": f"Catalog query failed ({catalog}): {e}",
                    "image": None, "catalog": catalog}

        for image in data.get("data", []):
            repos = image.get("repositories") or []
            for repo in repos:
                for t in repo.get("tags", []):
                    if assembly_pattern.search(t.get("name", "")):
                        metadata = _parse_image_metadata(image)
                        return {
                            "valid": True,
                            "reason": f"Image found in {catalog} catalog "
                                      f"(assembly {tag})",
                            "image": metadata,
                            "catalog": catalog,
                        }

        if len(data.get("data", [])) < 100:
            break

    return {"valid": False,
            "reason": f"Image for {version} not found in {catalog} catalog",
            "image": None, "catalog": catalog}


def _find_repo_id(catalog, repo_name):
    """Discover the Pyxis ObjectID for a container repository.

    Queries the REST API to find the repository by name,
    then caches the result for subsequent calls.

    Returns:
        str or None: The repository ObjectID.
    """
    cache_key = (catalog, repo_name)
    if cache_key in _repo_id_cache:
        return _repo_id_cache[cache_key]

    base = _REPO_BASE_URLS.get(catalog, _REPO_BASE_URLS["prod"])
    url = f"{base}/repositories"
    params = {"filter": f"repository=={repo_name}", "page_size": 1}
    try:
        resp = requests.get(url, params=params, timeout=15,
                            auth=_catalog_auth(catalog),
                            verify=(catalog != "stage"))
        if resp.status_code != 200:
            logger.warning("Repo search returned HTTP %d for %s on %s",
                           resp.status_code, repo_name, catalog)
            return None
        data = resp.json()
        items = data.get("data", [])
        if items:
            repo_id = items[0].get("_id")
            _repo_id_cache[cache_key] = repo_id
            return repo_id
    except (requests.RequestException, json.JSONDecodeError) as exc:
        logger.debug("Repo ID lookup failed for %s on %s: %s",
                     repo_name, catalog, exc)
    return None


def check_catalog_image_graphql(version, catalog="prod", arch="amd64", rhel=9):
    """Check if a bootc image exists in the catalog.

    Uses the Pyxis GraphQL API for prod, REST API for stage (no GraphQL
    on the stage Pyxis instance).

    Args:
        version: Full version string, e.g., "4.21.8".
        catalog: "prod" or "stage".
        arch: Architecture, e.g., "amd64" or "arm64".
        rhel: RHEL version (9 or 10).

    Returns:
        dict: {valid: bool, reason: str, image: dict | None, catalog: str}
    """
    if catalog == "stage":
        return check_catalog_image(version, catalog="stage", arch=arch, rhel=rhel)

    major = version.split(".")[0]
    repo_name = _BOOTC_REPO_NAME.format(major=major, rhel=rhel)
    repo_id = _find_repo_id(catalog, repo_name)
    if not repo_id:
        return {"valid": False,
                "reason": f"Repository {repo_name} not found in {catalog}",
                "image": None, "catalog": catalog}

    graphql_url = _GRAPHQL_URLS.get(catalog, _GRAPHQL_URLS["prod"])
    page_size = 250

    # GA/Z-stream: tags contain "assembly.X.Y.Z"
    # EC/RC: tags contain "vX.Y.Z-ec.N" or "vX.Y.Z-rc.N"
    base_version = re.sub(r"-(ec|rc)\.\d+$", "", version)
    tag_patterns = [
        re.compile(rf"\bassembly\.{re.escape(base_version)}\b"),
        re.compile(rf"^v{re.escape(version)}$"),
    ]

    for page in range(5):
        variables = {
            "id": repo_id,
            "page": page,
            "page_size": page_size,
            "filter": {
                "and": [
                    {"repositories_elemMatch": {
                        "and": [{"repository": {"eq": repo_name}}]
                    }}
                ]
            },
            "sort_by": [
                {"field": "repositories.push_date", "order": "DESC"},
                {"field": "repositories.repository", "order": "ASC"},
            ],
        }

        try:
            resp = requests.post(
                graphql_url,
                json={"query": _GRAPHQL_IMAGES_QUERY, "variables": variables},
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json()
        except (requests.RequestException, json.JSONDecodeError) as exc:
            return {"valid": False,
                    "reason": f"GraphQL query failed ({catalog}): {exc}",
                    "image": None, "catalog": catalog}

        data = result.get("data") or {}
        repo_data = (data.get("ContainerRepository") or {}).get("data") or {}
        edges = (repo_data.get("edges") or {})
        images_data = (edges.get("images") or {}).get("data", [])

        for image in images_data:
            if image.get("architecture") != arch:
                continue
            for repo in image.get("repositories", []):
                for t in repo.get("tags", []):
                    tag_name = t.get("name", "")
                    if any(p.search(tag_name) for p in tag_patterns):
                        all_tags = [{"name": tg.get("name", "")}
                                    for r in image.get("repositories", [])
                                    for tg in r.get("tags", [])]
                        base_meta = _parse_image_metadata(image)
                        metadata = {
                            "image_id": image.get("_id"),
                            "docker_image_digest": image.get("docker_image_digest"),
                            "commit_id": base_meta["commit_id"],
                            "commit_short": base_meta["commit_short"],
                            "freshness_grade": base_meta.get("freshness_grade"),
                            "container_grade_status": base_meta.get("container_grade_status"),
                            "tags": all_tags,
                            "matched_tag": tag_name,
                        }
                        return {
                            "valid": True,
                            "reason": f"Image found in {catalog} catalog (tag {tag_name})",
                            "image": metadata,
                            "catalog": catalog,
                        }

        if len(images_data) < page_size:
            break

    return {"valid": False,
            "reason": f"Image for {version} ({arch}) not found in {catalog} catalog",
            "image": None, "catalog": catalog}


def fetch_containerfile(commit_hash):
    """Fetch the Containerfile content from GitHub for a specific commit.

    Args:
        commit_hash: Full or short git commit hash.

    Returns:
        dict: {found: bool, content: str | None, url: str}
    """
    url = CONTAINERFILE_URL_TEMPLATE.format(commit=commit_hash)
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            return {"found": True, "content": resp.text, "url": url}
        return {"found": False, "content": None, "url": url}
    except requests.RequestException as e:
        logger.warning("Failed to fetch Containerfile at %s: %s", url, e)
        return {"found": False, "content": None, "url": url}
