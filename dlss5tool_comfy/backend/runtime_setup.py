"""Pinned, on-demand DLSSNR acquisition. No network at import/schema time."""

from contextlib import contextmanager
from datetime import date, timedelta
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import time
import threading
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import uuid
import zipfile

from .paths import ROOT, checked_path, identify, runtime_directory, require_exports


CATALOG = Path(__file__).with_name("runtime_catalog.json")
RUNTIME_EXPORTS = {"NVSDK_NGX_D3D12_Init_Ext", "NVSDK_NGX_D3D12_CreateFeature",
                   "NVSDK_NGX_D3D12_EvaluateFeature", "NVSDK_NGX_D3D12_ReleaseFeature"}
_REDIRECT_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
                   "github-releases.githubusercontent.com"}
_RELEASE_PREFIX = "https://github.com/banbanzhige/DLSS5Tool/releases/download/"
_fingerprint_lock = threading.Lock()
_fingerprints = {}
_generation = 0


def catalog():
    value = json.loads(checked_path(CATALOG).read_text(encoding="utf-8"))
    if value.get("schema_version") != 1:
        raise ValueError("Unsupported runtime catalog version")
    for revision in (value.get("catalog_revision"), value.get("host", {}).get("revision"),
                     *(p.get("revision") for p in value["profiles"])):
        if type(revision) is not int or revision < 1:
            raise ValueError("Runtime catalog requires positive component revisions")
    ids, capabilities = set(), set()
    for profile in value["profiles"]:
        key = profile["id"]
        if not key.isascii() or not key.isidentifier() or key in ids:
            raise ValueError("Invalid runtime profile identity")
        ids.add(key)
        url = urlsplit(profile["url"])
        if (url.scheme != "https" or url.hostname != "github.com" or url.username or url.password
                or not profile["url"].startswith(_RELEASE_PREFIX)):
            raise ValueError("Runtime download must use the pinned upstream release")
        member = PurePosixPath(profile["member"])
        if (member.is_absolute() or ".." in member.parts or "\\" in profile["member"]
                or member.name != "nvngx_dlssnr.dll"):
            raise ValueError("Unsafe runtime archive member")
        for key in ("archive_sha256", "dll_sha256"):
            digest = profile[key]
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("Invalid runtime checksum")
        for key in ("archive_size", "dll_size"):
            if type(profile[key]) is not int or not 0 < profile[key] <= 512 * 1024**2:
                raise ValueError("Invalid runtime size limit")
        for cc in profile["compute_capabilities"]:
            if type(cc) is not int or cc in capabilities:
                raise ValueError("Ambiguous GPU architecture mapping")
            capabilities.add(cc)
    return value


def profile_for(adapter):
    for profile in catalog()["profiles"]:
        if adapter.compute_capability in profile["compute_capabilities"]:
            return profile
    raise ValueError(f"No automatic DLSSNR profile for {adapter.name} (CUDA SM {adapter.compute_capability}); select a verified DLL manually")


def select_adapter(adapters, selection="auto"):
    if selection != "auto":
        match = next((a for a in adapters if selection == f"{a.name} | {a.id}"), None)
        if match is None:
            raise ValueError("Selected GPU is missing or incompatible")
        return match
    supported = {cc for p in catalog()["profiles"] for cc in p["compute_capabilities"]}
    candidates = [a for a in adapters if a.compute_capability in supported]
    if not candidates:
        raise ValueError("No compatible physical NVIDIA GPU with a known runtime profile was found")
    return min(candidates, key=lambda a: (-a.memory, a.id))


def cache_path(profile):
    root = runtime_directory()
    return checked_path(root / "cache" / (profile["id"] + "-" + profile["dll_sha256"][:16])
                        / "nvngx_dlssnr.dll", root=root, exists=False)


def verified_runtime(profile):
    path = cache_path(profile)
    if not path.exists():
        return None
    value = identify(path)
    if (value.size, value.sha256) != (profile["dll_size"], profile["dll_sha256"]):
        raise ValueError("The cached DLSSNR DLL does not match the catalog; it was preserved for inspection")
    require_exports(value, RUNTIME_EXPORTS)
    return value


def runtime_fingerprint(profile):
    """Initial acquisition is one execution; later replacements invalidate it.

    Missing→verified is the expected first-use transition and keeps its token.
    Replacing/removing an observed file creates a new generation, including
    same-content replacements whose file identity/timestamps changed.
    """
    global _generation
    identity = verified_runtime(profile)
    key = (str(cache_path(profile)), profile["dll_sha256"])
    with _fingerprint_lock:
        previous = _fingerprints.get(key)
        if previous is None or (previous[0] is not None and previous[0] != identity):
            _generation += 1
            token = _generation
        else:
            token = previous[1]
        if len(_fingerprints) >= 64 and key not in _fingerprints:
            del _fingerprints[next(iter(_fingerprints))]
        _fingerprints[key] = (identity, token)
    return key, token


@contextmanager
def _profile_lock(path, cancel):
    path = checked_path(path, exists=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + 360
        acquired = False
        try:
            while not acquired:
                cancel()
                stream.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Another DLSSNR setup is still running")
                    time.sleep(.1)
            yield
        finally:
            if acquired:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _space(path, needed):
    if shutil.disk_usage(path).free < 15 * 1024**3 + needed:
        raise OSError("Automatic DLSSNR setup needs the download budget plus 15 GiB of free space")


def _download(profile, path, cancel, progress):
    digest = hashlib.sha256()
    received = 0
    deadline = time.monotonic() + 300
    request = Request(profile["url"], headers={"User-Agent": "ComfyUI-DLSS5Tool-runtime/1"})
    logging.info("DLSS5: downloading fixed %s runtime (%.1f MiB)", profile["label"], profile["archive_size"] / 1024**2)
    with urlopen(request, timeout=10) as response, path.open("xb") as stream:
        redirect = urlsplit(response.geturl())
        if redirect.scheme != "https" or redirect.hostname not in _REDIRECT_HOSTS:
            raise ValueError("Unexpected runtime download redirect")
        length = response.headers.get("Content-Length")
        if length is not None and int(length) != profile["archive_size"]:
            raise ValueError("Runtime download size changed upstream")
        last_progress = -1
        while True:
            cancel()
            if time.monotonic() > deadline:
                raise TimeoutError("Runtime download exceeded its 300-second limit")
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            received += len(chunk)
            if received > profile["archive_size"]:
                raise ValueError("Runtime download exceeded its pinned size")
            stream.write(chunk)
            digest.update(chunk)
            percent = received * 100 // profile["archive_size"]
            if percent != last_progress:
                progress(received, profile["archive_size"])
                last_progress = percent
    if received != profile["archive_size"] or digest.hexdigest() != profile["archive_sha256"]:
        raise ValueError("Runtime archive checksum or size mismatch; no DLL was installed")


def _unpack(profile, archive_path, output, cancel):
    digest, size = hashlib.sha256(), 0
    with zipfile.ZipFile(archive_path) as archive:
        entries = [i for i in archive.infolist() if i.filename == profile["member"]]
        if len(entries) != 1:
            raise ValueError("Runtime archive member is missing or duplicated")
        entry = entries[0]
        if stat.S_ISLNK(entry.external_attr >> 16) or entry.is_dir() or entry.file_size != profile["dll_size"]:
            raise ValueError("Invalid runtime DLL archive member")
        with archive.open(entry) as source, output.open("xb") as target:
            while True:
                cancel()
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > profile["dll_size"]:
                    raise ValueError("Unpacked runtime exceeds its pinned size")
                target.write(chunk)
                digest.update(chunk)
    if (size, digest.hexdigest()) != (profile["dll_size"], profile["dll_sha256"]):
        raise ValueError("Unpacked runtime checksum mismatch; no DLL was installed")
    require_exports(identify(output), RUNTIME_EXPORTS)


def ensure_runtime(profile, cancel=None, progress=None):
    cancel = cancel or (lambda: None)
    progress = progress or (lambda current, total: None)
    cancel()
    if (existing := verified_runtime(profile)) is not None:
        return existing
    target = cache_path(profile)
    lock = checked_path(runtime_directory() / "cache" / (profile["id"] + ".lock"), exists=False)
    with _profile_lock(lock, cancel):
        if (existing := verified_runtime(profile)) is not None:
            return existing
        today = date.today()
        task = checked_path(ROOT / "tmp" / ("runtime-setup-" + today.strftime("%Y%m%d")), root=ROOT, exists=False)
        task.mkdir(parents=True, exist_ok=True)
        registration = checked_path(task / "TASK.md", root=task, exists=False)
        try:
            with registration.open("x", encoding="utf-8") as stream:
                stream.write(f"# DLSS5 automatic runtime setup\n\nOwner: DLSS5Tool. Created: {today}. Review: {today + timedelta(days=7)}.\n"
                             "Purpose: pinned runtime acquisition; budget below 512 MiB per setup plus 15 GiB free reserve.\n"
                             "Rebuild: execute Model Loader with auto. Per-run directories are cleaned after setup.\n")
        except FileExistsError:
            checked_path(registration, root=task)
        stage = checked_path(task / uuid.uuid4().hex, root=task, exists=False)
        stage.mkdir()
        files = [checked_path(stage / name, root=stage, exists=False) for name in ("archive.zip", "runtime.dll", "TASK.md")]
        files[2].write_text(f"# Runtime setup\n\nOwner: DLSS5Tool. Created: {today}. Review: {today + timedelta(days=7)}.\n"
                           f"Profile: {profile['id']}\nSource: {profile['url']}\nDLL SHA256: {profile['dll_sha256']}\n"
                           "Budget: below 512 MiB. Rebuild: execute Model Loader with auto. Remove after setup.\n", encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        pending = checked_path(target.parent / (".pending-" + uuid.uuid4().hex + ".dll"), root=runtime_directory(), exists=False)
        try:
            budget = profile["archive_size"] + 2 * profile["dll_size"]
            _space(stage, budget)
            _space(target.parent, budget)
            _download(profile, files[0], cancel, progress)
            _unpack(profile, files[0], files[1], cancel)
            cancel()
            # Cross-volume publication uses one owned transaction file beside
            # the target, then an atomic rename. Existing DLLs are never replaced.
            checked_path(files[1], root=stage)
            checked_path(pending, root=runtime_directory(), exists=False)
            with files[1].open("rb") as source, pending.open("xb") as destination:
                while True:
                    cancel()
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            identity = identify(pending)
            if (identity.size, identity.sha256) != (profile["dll_size"], profile["dll_sha256"]):
                raise ValueError("Runtime publication checksum mismatch")
            cancel()
            checked_path(target, root=runtime_directory(), exists=False)
            if target.exists():
                raise RuntimeError("The target runtime appeared during setup; it was preserved")
            os.replace(pending, target)
            logging.info("DLSS5: installed verified %s runtime", profile["label"])
            return verified_runtime(profile)
        finally:
            for file in (*files, pending):
                if file.exists():
                    checked_path(file, root=stage if file in files else runtime_directory()).unlink()
            stage.rmdir()
