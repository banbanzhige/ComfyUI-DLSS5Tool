"""Read-only, standard-library checks for the maintained project foundation."""

import argparse
import ast
import json
import os
from pathlib import Path
import re
import stat
import hashlib
from urllib.parse import unquote, urlsplit


REQUIRED_FILES = (
    "CONTRIBUTING.md",
    "SECURITY.md",
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "docs/README.md",
    "docs/USAGE.md",
    "docs/DEVELOPING.md",
    "docs/provenance/SOURCE_MANIFEST.json",
    ".github/pull_request_template.md",
    ".github/workflows/checks.yml",
    "scripts/verify_handoff.py",
    "scripts/run_reference_checks.py",
    "runtime/README.md",
    "third_party/README.md",
)
LOCAL_ROOTS = {
    ".git", ".venv", "venv", "env", "tmp", "build", "dist", "output", "var",
    "runtime", "third_party", ".codex", ".agents",
}
CACHE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
ORIGINAL_TREES = {("reference", "upstream"), ("docs", "provenance")}
INTERNAL_TREES = {("docs", name) for name in ("development", "design", "templates", "release")}
INTERNAL_FILES = {"agents.md", "handoff_manifest.json", "docs/handoff.md", "docs/development_plan.md"}
FORBIDDEN_SUFFIXES = {
    ".dll", ".exe", ".lib", ".obj", ".pdb", ".exp", ".ilk", ".log",
    ".zip", ".7z", ".dlssupdate", ".pt", ".pth", ".safetensors", ".pyc",
}
MAX_SOURCE_BYTES = 5 * 1024 * 1024
LINK = re.compile(
    r"""!?\[[^\]\n]*\]\(\s*(?:<(?P<angle>[^>\n]+)>|(?P<plain>[^\s)\n]+))"""
    r"""(?:\s+["'][^\n]*?["'])?\s*\)"""
)
FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


def checked_path(path, root=None):
    """Reject reparse points on the path and every ancestor before reading."""
    path = Path(path).absolute()
    for entry in (path, *path.parents):
        info = entry.lstat()
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
        ):
            raise ValueError(f"Reparse point rejected: {entry}")
    resolved = path.resolve(strict=True)
    if root is not None and not resolved.is_relative_to(root):
        raise ValueError(f"Path outside project: {path}")
    return resolved


def internal_document(relative):
    parts = tuple(part.casefold() for part in relative.parts)
    return parts[:2] in INTERNAL_TREES or "/".join(parts) in INTERNAL_FILES


def excluded(relative):
    parts = tuple(part.casefold() for part in relative.parts)
    return (
        bool(parts and parts[0] in LOCAL_ROOTS)
        or any(part in CACHE_DIRS for part in parts)
        or parts[:2] in ORIGINAL_TREES
        or internal_document(relative)
    )


def maintained_files(root):
    files = []

    def walk_error(error):
        raise error

    for directory, names, filenames in os.walk(root, followlinks=False, onerror=walk_error):
        base = checked_path(Path(directory), root)
        kept = []
        for name in sorted(names):
            child = base / name
            if not excluded(child.relative_to(root)):
                checked_path(child, root)
                kept.append(name)
        names[:] = kept
        for name in sorted(filenames):
            path = base / name
            if not excluded(path.relative_to(root)):
                files.append(checked_path(path, root))
    # Local dependencies remain excluded; their committed instructions are checked.
    for relative in ("runtime/README.md", "runtime/dlssnr_host_v2.dll", "third_party/README.md"):
        files.append(checked_path(root / relative, root))
    return sorted(set(files))


def markdown_links(markdown):
    """Recognize inline Markdown links outside fenced examples."""
    fence = None
    for line_number, line in enumerate(markdown.splitlines(), 1):
        marker = FENCE.match(line)
        if marker:
            value = marker.group(1)
            if fence is None:
                fence = (value[0], len(value))
            elif value[0] == fence[0] and len(value) >= fence[1]:
                fence = None
            continue
        if fence is None:
            for match in LINK.finditer(line):
                yield line_number, match.group("angle") or match.group("plain")


def check_link(document, target, root):
    parsed = urlsplit(target)
    if parsed.scheme.casefold() in {"https", "http", "mailto"}:
        return
    if parsed.scheme or parsed.netloc or parsed.path.startswith(("/", "\\")):
        raise ValueError(f"Use a project-relative local link: {target}")
    if parsed.path:
        resolved = checked_path(document.parent / unquote(parsed.path), root)
        if internal_document(resolved.relative_to(root)):
            raise ValueError("Public document links to local maintenance material")


def production_boundary_errors(tree):
    errors = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""] if node.level == 0 else []
        else:
            names = []
        for name in names:
            if name.split(".")[0] in {"dlss5tool", "reference"}:
                errors.append((node.lineno, f"Legacy/reference import: {name}"))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.replace("\\", "/").casefold()
            parsed = urlsplit(value)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                continue  # A declared network source is not a legacy filesystem import.
            if "reference/upstream" in value or re.search(r"(^|/)dlss5tool(/|$)", value):
                errors.append((node.lineno, "Legacy/reference path in production code"))
    return errors


def source_file_errors(path, size, *, bundled_host=False):
    name = path.name.casefold()
    private_config = (name == ".env" or name.startswith(".env.")) and name != ".env.example"
    split_archive = re.search(r"\.(?:zip|7z)\.\d{3}$", name)
    errors = []
    if (path.suffix.casefold() in FORBIDDEN_SUFFIXES and not bundled_host) or private_config or split_archive:
        errors.append("artifact or private configuration in maintained source")
    if size > MAX_SOURCE_BYTES:
        errors.append("source file exceeds the 5 MiB review limit")
    return errors


def check(root):
    root = checked_path(root)
    errors = []
    try:
        spec = json.loads((root / "dlss5tool_comfy/backend/runtime_catalog.json").read_text(encoding="utf-8"))["host"]
        host = checked_path(root / "runtime/dlssnr_host_v2.dll", root)
        if spec["path"] != "runtime/dlssnr_host_v2.dll":
            raise ValueError("Unexpected bundled host path")
        data = host.read_bytes()
        if len(data) != spec["size"] or hashlib.sha256(data).hexdigest() != spec["sha256"] or data[:2] != b"MZ":
            raise ValueError("Bundled host size, SHA256 or PE header mismatch")
        checked_path(root / spec["license"], root)
    except (OSError, KeyError, ValueError) as error:
        errors.append(f"Bundled host: {error}")
    for relative in REQUIRED_FILES:
        try:
            if not checked_path(root / relative, root).is_file():
                raise ValueError("Expected a file")
        except (OSError, ValueError) as error:
            errors.append(f"{relative}: {error}")
    # Stop before traversal if a required dependency directory is unsafe or absent.
    if errors:
        return errors, {}
    files = maintained_files(root)
    counts = {"files": len(files), "python": 0, "json": 0, "links": 0}
    for path in files:
        relative = path.relative_to(root)
        label = relative.as_posix()
        suffix = path.suffix.casefold()
        size = path.stat().st_size
        for error in source_file_errors(path, size, bundled_host=label == "runtime/dlssnr_host_v2.dll"):
            errors.append(f"{label}: {error}")
        if size > MAX_SOURCE_BYTES:
            continue
        try:
            if suffix == ".py":
                tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=label)
                counts["python"] += 1
                if label == "__init__.py" or relative.parts[0] == "dlss5tool_comfy":
                    for line_number, message in production_boundary_errors(tree):
                        errors.append(f"{label}:{line_number}: {message}")
            elif suffix == ".json":
                json.loads(path.read_text(encoding="utf-8-sig"))
                counts["json"] += 1
            elif suffix == ".md":
                for line_number, target in markdown_links(path.read_text(encoding="utf-8-sig")):
                    counts["links"] += 1
                    try:
                        check_link(path, target, root)
                    except (OSError, ValueError) as error:
                        errors.append(f"{label}:{line_number}: {error}")
        except (OSError, ValueError, SyntaxError) as error:
            errors.append(f"{label}: {error}")
    return errors, counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).absolute().parents[1])
    args = parser.parse_args()
    try:
        errors, counts = check(args.root)
    except (OSError, ValueError) as error:
        errors, counts = [str(error)], {}
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print(
        "PASS: {files} maintained files; {python} Python, {json} JSON, "
        "{links} inline Markdown links; source boundaries and artifact checks.".format(**counts)
    )
    print("Static scope only; this does not validate a working plugin or real GPU execution.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
