"""Read-only audit of Git's index before a GitHub upload.

Reads staged blob bytes, including files later modified in the working tree.
No GitHub access, credential access, network calls or output files are needed.
"""

from pathlib import Path, PurePosixPath
import re
import hashlib
import json
import subprocess
import sys


ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from check_project import checked_path, internal_document
LOCAL_ROOTS = {".git", ".venv", "venv", "env", "tmp", "build", "dist", "output", "var",
               ".codex", ".agents", ".aws", ".ssh"}
CACHES = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".cache"}
FORBIDDEN = {".dll", ".exe", ".lib", ".obj", ".pdb", ".exp", ".ilk", ".log", ".pyc",
             ".zip", ".7z", ".rar", ".dlssupdate", ".pt", ".pth", ".safetensors",
             ".pem", ".key", ".pfx", ".p12"}
PRIVATE_NAMES = {"id_rsa", "id_ed25519", "credentials.json", "secrets.json"}
MAX_FILE_BYTES = 5 * 1024 * 1024
SECRET = re.compile(
    rb"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    rb"AKIA[A-Z0-9]{16}|sk-[A-Za-z0-9_-]{24,})\b|"
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)


def path_errors(mode, name):
    path = PurePosixPath(name)
    parts = tuple(part.casefold() for part in path.parts)
    errors = []
    if mode != "100644" and mode != "100755":
        errors.append("symlink, submodule or unsupported Git mode")
    if not parts or path.is_absolute() or ".." in parts or "\\" in name:
        errors.append("unsafe repository path")
        return errors
    if parts[0] in LOCAL_ROOTS or any(part in CACHES for part in parts):
        errors.append("local state or generated directory")
    if internal_document(path):
        errors.append("local maintenance document or collaboration record")
    host = name == "runtime/dlssnr_host_v2.dll"
    if parts[0] in {"runtime", "third_party"} and parts != (parts[0], "readme.md") and not host:
        errors.append("runtime binary or SDK directory")
    if (path.suffix.casefold() in FORBIDDEN and not host) or re.search(r"\.(?:zip|7z)\.\d{3}$", name, re.I):
        errors.append("binary, archive, model or credential file")
    if path.name.casefold() in PRIVATE_NAMES or (
        path.name.casefold().startswith(".env") and path.name.casefold() != ".env.example"
    ):
        errors.append("private configuration")
    return errors


def git(*args, input=None):
    result = subprocess.run(["git", "-c", f"safe.directory={ROOT}", *args], cwd=ROOT,
                            input=input, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("Git index is unavailable; initialize the local repository and stage its intended files first")
    return result.stdout


def audit():
    checked_path(ROOT)
    checked_path(ROOT / ".git", ROOT)
    entries = []
    errors = []
    for record in git("ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        header, raw_name = record.split(b"\t", 1)
        mode, oid, stage = header.decode("ascii").split()
        name = raw_name.decode("utf-8")
        for reason in path_errors(mode, name):
            errors.append(f"{name}: {reason}")
        if stage != "0":
            errors.append(f"{name}: unresolved Git merge stage")
        try:
            checked_path(ROOT / name, ROOT)
        except (OSError, ValueError) as error:
            errors.append(f"{name}: {error}")
        entries.append((name, oid))
    if not entries:
        errors.append("Git index is empty")
    if errors:
        return errors, 0, 0
    host_spec = json.loads(git("show", ":dlss5tool_comfy/backend/runtime_catalog.json"))["host"]
    if host_spec["path"] != "runtime/dlssnr_host_v2.dll":
        raise ValueError("Unexpected bundled host path in staged catalog")
    if not any(name == host_spec["path"] for name, _ in entries):
        raise ValueError("Bundled v2 host is missing from the index")
    # One object at a time keeps memory bounded by the existing 5 MiB source
    # limit. The bytes come from the index, not from potentially newer files.
    total = 0
    process = subprocess.Popen(["git", "-c", f"safe.directory={ROOT}", "cat-file", "--batch"],
                               cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
    try:
        for name, oid in entries:
            process.stdin.write((oid + "\n").encode("ascii"))
            process.stdin.flush()
            header = process.stdout.readline(256).split()
            if len(header) != 3 or header[1] != b"blob":
                raise RuntimeError("Invalid Git blob response")
            size = int(header[2])
            if not 0 <= size <= MAX_FILE_BYTES:
                errors.append(f"{name}: exceeds the 5 MiB source review limit")
                break
            data = process.stdout.read(size)
            if len(data) != size or process.stdout.read(1) != b"\n":
                raise RuntimeError("Incomplete Git blob response")
            total += size
            if name == host_spec["path"] and (
                size != host_spec["size"] or hashlib.sha256(data).hexdigest() != host_spec["sha256"] or data[:2] != b"MZ"
            ):
                errors.append(f"{name}: bundled host checksum or size mismatch")
            if SECRET.search(data):
                errors.append(f"{name}: possible credential; inspect locally before uploading")
            if name.startswith("workflows/") and name.endswith(".json"):
                text = data.decode("utf-8")
                if '"is_changed"' in text or re.search(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]", text):
                    errors.append(f"{name}: execution cache or machine-specific path in shared workflow")
        process.stdin.close()
        if errors:
            process.terminate()
        elif process.wait(timeout=5) != 0:
            raise RuntimeError("Git blob audit failed")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()
    return errors, len(entries), total


def main():
    try:
        errors, count, size = audit()
    except (OSError, ValueError, RuntimeError) as error:
        errors, count, size = [str(error)], 0, 0
    for error in errors:
        print(f"FAIL: {error}")
    if errors:
        return 1
    print(f"PASS: {count} staged files, {size} bytes; no excluded artifacts or recognized credential patterns.")
    print("Local index audit only; separately review history, external artifacts and intended visibility before uploading.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
