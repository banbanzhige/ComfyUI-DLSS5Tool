"""Pinned download, architecture selection and cache invalidation contracts."""

from dataclasses import replace
from datetime import date, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import threading
import unittest
from unittest.mock import patch
import uuid
import warnings
import zipfile

from dlss5tool_comfy.backend import runtime_setup as setup
from dlss5tool_comfy.backend.paths import checked_path
from dlss5tool_comfy.core.contracts import Adapter, FileIdentity


class Response(io.BytesIO):
    def __init__(self, value, final_url="https://release-assets.githubusercontent.com/test.zip", read_hook=None):
        super().__init__(value)
        self.headers = {"Content-Length": str(len(value))}
        self.final_url, self.read_hook = final_url, read_hook

    def geturl(self):
        return self.final_url

    def read(self, size=-1):
        result = super().read(size)
        if self.read_hook:
            self.read_hook()
        return result


def gpu(key, memory, cc):
    return Adapter(key, key, memory, 0, memory, cc)


class RuntimeSetupTests(unittest.TestCase):
    def setUp(self):
        project = Path(__file__).resolve().parents[1]
        task = checked_path(project / "tmp" / ("runtime-tests-" + date.today().strftime("%Y%m%d")),
                            root=project, exists=False)
        task.mkdir(parents=True, exist_ok=True)
        registration = checked_path(task / "TASK.md", root=task, exists=False)
        if not registration.exists():
            registration.write_text(f"# Runtime CPU tests\n\nOwner: run_plugin_checks.py. Created: {date.today()}. Review: {date.today() + timedelta(days=7)}.\n"
                                    "Synthetic fixtures, below 1 MiB per case. Rebuild: python -B scripts/run_plugin_checks.py.\n"
                                    "Per-case data is removed after each test.\n", encoding="utf-8")
        self.root = checked_path(task / uuid.uuid4().hex, root=task, exists=False)
        self.root.mkdir()
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.real_space = setup._space
        self.patches = [patch.object(setup, "ROOT", self.root),
                        patch.object(setup, "runtime_directory", return_value=self.runtime),
                        patch.object(setup, "require_exports"), patch.object(setup, "_space")]
        for item in self.patches:
            item.start()
        dll = bytearray(256)
        dll[:2] = b"MZ"
        struct.pack_into("<I", dll, 60, 64)
        dll[64:70] = b"PE\0\0\x64\x86"
        self.dll = bytes(dll)
        self.profile, self.archive = self.fixture()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        for directory, names, files in os.walk(self.root, followlinks=False):
            checked_path(directory, root=self.root)
            for name in (*names, *files):
                checked_path(Path(directory) / name, root=self.root)
        checked_path(self.root, root=self.root.parent)
        shutil.rmtree(self.root)

    def fixture(self, duplicate=False):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("nested/nvngx_dlssnr.dll", self.dll)
            if duplicate:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    archive.writestr("nested/nvngx_dlssnr.dll", self.dll)
        data = buffer.getvalue()
        return {"id": "test", "label": "test", "compute_capabilities": [89],
                "url": "https://github.com/banbanzhige/DLSS5Tool/releases/download/test/test.zip",
                "archive_size": len(data), "archive_sha256": hashlib.sha256(data).hexdigest(),
                "member": "nested/nvngx_dlssnr.dll", "dll_size": len(self.dll),
                "dll_sha256": hashlib.sha256(self.dll).hexdigest()}, data

    def test_architecture_profiles_and_unknown_gpu(self):
        self.assertEqual(setup.profile_for(gpu("RTX 30", 24, 86))["id"], "ampere")
        self.assertEqual(setup.profile_for(gpu("RTX 40", 12, 89))["id"], "ada")
        self.assertEqual(setup.profile_for(gpu("RTX 50", 16, 120))["id"], "blackwell")
        for cc in (0, 75, 90):
            with self.assertRaises(ValueError):
                setup.profile_for(gpu("unknown", 16, cc))

    def test_auto_gpu_is_deterministic_and_manual_choice_is_preserved(self):
        values = [gpu("b", 24, 86), gpu("a", 24, 89), gpu("z", 12, 120), gpu("unknown", 64, 0)]
        self.assertEqual(setup.select_adapter(values).id, "a")
        self.assertEqual(setup.select_adapter(values[::-1]).id, "a")
        self.assertEqual(setup.select_adapter(values, "z | z").id, "z")
        with self.assertRaises(ValueError):
            setup.select_adapter(values, "missing")

    def test_download_verifies_and_reuses_cache_without_network(self):
        with patch.object(setup, "urlopen", return_value=Response(self.archive)) as request:
            value = setup.ensure_runtime(self.profile)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(Path(value.path).read_bytes(), self.dll)
        with patch.object(setup, "urlopen", side_effect=AssertionError("cache must be offline")):
            self.assertEqual(setup.ensure_runtime(self.profile), value)
        self.assertFalse(list((self.root / "tmp").glob("runtime-setup-*/*/archive.zip")))

    def test_wrong_archive_digest_does_not_publish(self):
        profile = dict(self.profile, archive_sha256="0" * 64)
        with patch.object(setup, "urlopen", return_value=Response(self.archive)), self.assertRaisesRegex(ValueError, "archive checksum"):
            setup.ensure_runtime(profile)
        self.assertFalse(setup.cache_path(profile).exists())

    def test_wrong_dll_digest_does_not_publish(self):
        profile = dict(self.profile, dll_sha256="0" * 64)
        with patch.object(setup, "urlopen", return_value=Response(self.archive)), self.assertRaisesRegex(ValueError, "runtime checksum"):
            setup.ensure_runtime(profile)
        self.assertFalse(setup.cache_path(profile).exists())

    def test_truncated_download_does_not_publish(self):
        response = Response(self.archive[:-2])
        response.headers = {}
        with patch.object(setup, "urlopen", return_value=response), self.assertRaisesRegex(ValueError, "archive checksum"):
            setup.ensure_runtime(self.profile)
        self.assertFalse(setup.cache_path(self.profile).exists())

    def test_untrusted_redirect_is_rejected(self):
        response = Response(self.archive, "https://other.example/runtime.zip")
        with patch.object(setup, "urlopen", return_value=response), self.assertRaisesRegex(ValueError, "redirect"):
            setup.ensure_runtime(self.profile)

    def test_duplicate_archive_member_is_rejected(self):
        profile, archive = self.fixture(duplicate=True)
        with patch.object(setup, "urlopen", return_value=Response(archive)), self.assertRaisesRegex(ValueError, "duplicated"):
            setup.ensure_runtime(profile)

    def test_corrupt_existing_dll_is_preserved(self):
        path = setup.cache_path(self.profile)
        path.parent.mkdir(parents=True)
        path.write_bytes(self.dll[:-1])
        with patch.object(setup, "urlopen", side_effect=AssertionError("do not replace unknown files")), self.assertRaisesRegex(ValueError, "preserved"):
            setup.ensure_runtime(self.profile)
        self.assertEqual(path.read_bytes(), self.dll[:-1])

    def test_publication_failure_preserves_old_version_and_removes_transaction(self):
        old_bytes = self.dll[:-1] + b"1"
        old_profile = dict(self.profile, dll_sha256=hashlib.sha256(old_bytes).hexdigest())
        old_path = setup.cache_path(old_profile)
        old_path.parent.mkdir(parents=True)
        old_path.write_bytes(old_bytes)
        with patch.object(setup, "urlopen", return_value=Response(self.archive)), \
                patch.object(setup.os, "replace", side_effect=OSError("publication failed")), self.assertRaises(OSError):
            setup.ensure_runtime(self.profile)
        self.assertEqual(setup.verified_runtime(old_profile).sha256, old_profile["dll_sha256"])
        self.assertFalse(setup.cache_path(self.profile).exists())
        self.assertFalse(list(self.runtime.rglob(".pending-*.dll")))

    def test_download_larger_than_catalog_is_rejected(self):
        response = Response(self.archive + b"extra data")
        response.headers = {}
        with patch.object(setup, "urlopen", return_value=response), self.assertRaisesRegex(ValueError, "pinned size"):
            setup.ensure_runtime(self.profile)
        self.assertFalse(setup.cache_path(self.profile).exists())

    def test_low_space_fails_before_network(self):
        with patch.object(setup.shutil, "disk_usage") as usage, patch.object(setup, "urlopen") as request, \
                patch.object(setup, "_space", side_effect=self.real_space):
            usage.return_value.free = 14 * 1024**3
            with self.assertRaisesRegex(OSError, "15 GiB"):
                setup.ensure_runtime(self.profile)
            request.assert_not_called()

    def test_cancellation_during_download_removes_unverified_data(self):
        event = threading.Event()

        def cancel():
            if event.is_set():
                raise InterruptedError("cancelled")

        with patch.object(setup, "urlopen", return_value=Response(self.archive, read_hook=event.set)), self.assertRaises(InterruptedError):
            setup.ensure_runtime(self.profile, cancel)
        self.assertFalse(setup.cache_path(self.profile).exists())
        self.assertFalse(list((self.root / "tmp").glob("runtime-setup-*/*/archive.zip")))

    def test_parallel_setup_downloads_once(self):
        values, errors = [], []

        def run():
            try:
                values.append(setup.ensure_runtime(self.profile))
            except Exception as error:
                errors.append(error)

        with patch.object(setup, "urlopen", side_effect=lambda *a, **k: Response(self.archive)) as request:
            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
            self.assertFalse(any(t.is_alive() for t in threads))
            self.assertFalse(errors, errors)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(values[0], values[1])

    def test_fingerprint_first_acquisition_is_stable_and_replacement_invalidates(self):
        one = FileIdentity("runtime", 256, 1, 1, 1, self.profile["dll_sha256"])
        two = replace(one, mtime_ns=2)
        with patch.dict(setup._fingerprints, {}, clear=True), \
                patch.object(setup, "verified_runtime", side_effect=[None, one, one, two, None, one]):
            values = [setup.runtime_fingerprint(self.profile) for _ in range(6)]
        self.assertEqual(values[0], values[1])
        self.assertEqual(values[1], values[2])
        self.assertNotEqual(values[2], values[3])
        self.assertNotEqual(values[3], values[4])
        self.assertEqual(values[4], values[5])

    def test_catalog_rejects_unsafe_sources_and_members(self):
        data = setup.catalog()
        path = self.root / "catalog.json"
        for changes in ({"url": "https://other.example/runtime.zip"}, {"member": "../nvngx_dlssnr.dll"},
                        {"archive_size": -1}, {"dll_sha256": "bad"}):
            value = json.loads(json.dumps(data))
            value["profiles"][0].update(changes)
            path.write_text(json.dumps(value), encoding="utf-8")
            with patch.object(setup, "CATALOG", path), self.assertRaises(ValueError):
                setup.catalog()


if __name__ == "__main__":
    unittest.main()
