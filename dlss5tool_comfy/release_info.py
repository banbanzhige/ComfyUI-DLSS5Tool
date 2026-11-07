"""Offline release identity. Revisions are project pins, not vendor versions."""

import ast
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import sys

from . import __version__
from .backend.paths import ROOT, checked_path


CATALOG_PATH = "dlss5tool_comfy/backend/runtime_catalog.json"
REQUIREMENTS_PATH = "requirements.txt"
SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z.-]+))?")


def version_key(version):
    match = SEMVER.fullmatch(version) if isinstance(version, str) else None
    if not match:
        raise ValueError("Use a SemVer version such as 0.1.0-beta.1")
    prerelease = match[4]
    parts = prerelease.split(".") if prerelease else []
    if any(not p or (p.isdigit() and len(p) > 1 and p[0] == "0") for p in parts):
        raise ValueError("Invalid prerelease version")
    return (*map(int, match.group(1, 2, 3)), prerelease is None,
            tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts))


def source_version(data):
    values = [ast.literal_eval(n.value) for n in ast.parse(data).body
              if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in n.targets)]
    if len(values) != 1:
        raise ValueError("Exactly one literal __version__ is required")
    version_key(values[0])
    return values[0]


def _revision(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive project revision")


def validate_release(release, catalog_bytes, requirements_bytes, version):
    if release.get("schema_version") != 1:
        raise ValueError("Unsupported version.json schema")
    version_key(release["plugin_version"])
    if release["plugin_version"] != version:
        raise ValueError("Plugin version and version.json disagree; update the complete plugin")
    for key, path, data in (("runtime_catalog", CATALOG_PATH, catalog_bytes),
                            ("python_requirements", REQUIREMENTS_PATH, requirements_bytes)):
        pin = release[key]
        if pin["path"] != path or pin["sha256"] != hashlib.sha256(data).hexdigest():
            raise ValueError(f"Release pin mismatch: {path}; update the complete plugin")
    catalog = json.loads(catalog_bytes)
    _revision(catalog["catalog_revision"], "Catalog revision")
    _revision(release["runtime_catalog"]["revision"], "Release catalog revision")
    if catalog["catalog_revision"] != release["runtime_catalog"]["revision"]:
        raise ValueError("Catalog revision and version.json disagree")
    _revision(catalog["host"]["revision"], "Host revision")
    if catalog["host"]["abi"] != 2:
        raise ValueError("This plugin requires host ABI 2")
    for profile in catalog["profiles"]:
        _revision(profile["revision"], "NR revision")
    return catalog


def load_release(root=ROOT):
    root = checked_path(root)
    release = json.loads(checked_path(root / "version.json", root=root).read_bytes())
    catalog = validate_release(release,
                               checked_path(root / CATALOG_PATH, root=root).read_bytes(),
                               checked_path(root / REQUIREMENTS_PATH, root=root).read_bytes(), __version__)
    return release, catalog


def startup_identity():
    release, catalog = load_release()
    host = catalog["host"]
    return f"DLSS5Tool v{release['plugin_version']} | catalog r{catalog['catalog_revision']} | host ABI {host['abi']} r{host['revision']}"


def version_report():
    """No network/GPU, private paths, or image data in the shared report."""
    from .backend.runtime_setup import verified_runtime

    release, catalog = load_release()
    host = catalog["host"]
    data = checked_path(ROOT / host["path"], root=ROOT).read_bytes()
    host_ok = len(data) == host["size"] and hashlib.sha256(data).hexdigest() == host["sha256"]
    runtimes = []
    for profile in catalog["profiles"]:
        try:
            installed = verified_runtime(profile)
            status = "verified" if installed else "not_downloaded"
        except (OSError, ValueError, RuntimeError):
            status = "invalid"
        runtimes.append({"profile": profile["id"], "revision": profile["revision"],
                         "sha256": profile["dll_sha256"], "cache_status": status})
    packages = []
    for line in checked_path(ROOT / REQUIREMENTS_PATH, root=ROOT).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, separator, expected = line.partition("==")
        if not separator:
            raise ValueError("Version report expects pinned Python requirements")
        try:
            installed = metadata.version(name.strip())
        except metadata.PackageNotFoundError:
            installed = None
        packages.append({"package": name.strip(), "required": expected.strip(), "installed": installed})
    return {"plugin_version": release["plugin_version"], "catalog_revision": catalog["catalog_revision"],
            "host": {"revision": host["revision"], "abi": host["abi"], "sha256": host["sha256"], "verified": host_ok},
            "runtimes": runtimes, "python": sys.version.split()[0], "python_requirements": packages}
