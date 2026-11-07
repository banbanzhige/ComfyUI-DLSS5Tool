"""Check release pins and version increments against a Git baseline; no network."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dlss5tool_comfy.release_info import CATALOG_PATH, load_release, source_version, version_key


def production_path(name):
    return (name in {"__init__.py", "version.json", "requirements.txt", "workflows/node-contract.json"}
            or name.startswith(("dlss5tool_comfy/", "native/host_v2/", "workflows/examples/", "workflows/planned/"))
            or name == "runtime/dlssnr_host_v2.dll")


def update_errors(previous, old_catalog, current, catalog, changed_paths):
    errors = []
    dependency_change = (old_catalog != catalog or previous["python_requirements"] != current["python_requirements"])
    if (dependency_change or any(map(production_path, changed_paths))) and version_key(current["plugin_version"]) <= version_key(previous["plugin_version"]):
        errors.append("Production code or dependencies changed: increase the plugin version")
    if old_catalog != catalog and catalog["catalog_revision"] <= old_catalog["catalog_revision"]:
        errors.append("Runtime catalog changed: increase catalog_revision")
    old_host, host = old_catalog["host"], catalog["host"]
    if host["revision"] < old_host["revision"] or (
        any(old_host[k] != host[k] for k in ("sha256", "size", "abi")) and host["revision"] <= old_host["revision"]
    ):
        errors.append("Host identity changed: increase its revision")
    old_profiles = {p["id"]: p for p in old_catalog["profiles"]}
    fields = ("compute_capabilities", "url", "archive_size", "archive_sha256", "member", "dll_size", "dll_sha256")
    for profile in catalog["profiles"]:
        old = old_profiles.get(profile["id"])
        if old and (profile["revision"] < old["revision"] or (
            any(profile[k] != old[k] for k in fields) and profile["revision"] <= old["revision"]
        )):
            errors.append(f"NR {profile['id']} identity changed: increase its revision")
    return errors


def git(*args, check=True):
    result = subprocess.run(["git", "-c", f"safe.directory={ROOT}", *args], cwd=ROOT,
                            capture_output=True, check=False)
    if check and result.returncode:
        raise ValueError("Version baseline is unavailable; fetch the requested history before checking")
    return result


def check(base="", ref=""):
    release, catalog = load_release(ROOT)
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if not any(line.startswith("## v" + release["plugin_version"] + " ") for line in changelog.splitlines()):
        raise ValueError("Add a CHANGELOG entry for the current version")
    if ref.startswith("refs/tags/") and ref != "refs/tags/v" + release["plugin_version"]:
        raise ValueError("Git tag and installed plugin version disagree")
    if not base or set(base) == {"0"}:
        return release, []
    resolved = git("rev-parse", "--verify", "--end-of-options", base + "^{commit}").stdout.decode().strip()
    if git("cat-file", "-e", resolved + ":version.json", check=False).returncode:
        return release, []  # First versioned release over an unversioned bootstrap.
    previous = json.loads(git("show", resolved + ":version.json").stdout)
    old_catalog = json.loads(git("show", resolved + ":" + CATALOG_PATH).stdout)
    changed = git("diff", "--name-only", "-z", resolved, "--").stdout.decode().split("\0")
    errors = update_errors(previous, old_catalog, release, catalog, changed)
    if source_version(git("show", resolved + ":dlss5tool_comfy/__init__.py").stdout) != previous["plugin_version"]:
        errors.append("Baseline version metadata is inconsistent")
    return release, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.environ.get("VERSION_BASE", ""), help="Previous release/tag or PR base")
    parser.add_argument("--ref", default=os.environ.get("GITHUB_REF", ""), help="Tag ref to compare with version.json")
    args = parser.parse_args()
    try:
        release, errors = check(args.base, args.ref)
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors = [str(error)]
    for error in errors:
        print("FAIL: " + error)
    if errors:
        return 1
    print(f"PASS: v{release['plugin_version']}, catalog r{release['runtime_catalog']['revision']}; release pins and version policy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
