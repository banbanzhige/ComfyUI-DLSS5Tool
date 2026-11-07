"""Offline identity pairing and forgotten-version upgrade gates."""

from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

from dlss5tool_comfy import release_info as info
from dlss5tool_comfy.backend import runtime_setup
from dlss5tool_comfy.backend.paths import checked_path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_versions as policy


class VersioningTests(unittest.TestCase):
    def setUp(self):
        self.release, self.catalog = info.load_release()
        self.release["plugin_version"] = "1.2.3-beta.1"  # Synthetic baseline, independent of this release's number.
        self.catalog_bytes = (info.ROOT / info.CATALOG_PATH).read_bytes()
        self.requirements = (info.ROOT / info.REQUIREMENTS_PATH).read_bytes()

    def bumped(self):
        release = deepcopy(self.release)
        release["plugin_version"] = "1.2.3-beta.2"
        return release

    def test_semver_orders_beta_and_stable_and_rejects_invalid_versions(self):
        values = ["0.1.0-alpha.1", "0.1.0-beta.2", "0.1.0-beta.10", "0.1.0-rc.1", "0.1.0", "0.1.1"]
        self.assertEqual(sorted(values, key=info.version_key), values)
        for value in ("01.1.0", "0.1.0-beta.01", "0.1.0-beta..1", "v0.1.0", "0.1"):
            with self.assertRaises(ValueError):
                info.version_key(value)

    def test_release_rejects_catalog_mixed_from_another_plugin_update(self):
        with self.assertRaisesRegex(ValueError, "Release pin mismatch"):
            info.validate_release(self.release, self.catalog_bytes + b"\n", self.requirements, self.release["plugin_version"])

    def test_release_rejects_requirements_mixed_from_another_update(self):
        with self.assertRaisesRegex(ValueError, "Release pin mismatch"):
            info.validate_release(self.release, self.catalog_bytes, self.requirements + b"\n", self.release["plugin_version"])

    def test_release_rejects_mixed_code_version_and_noninteger_revisions(self):
        with self.assertRaisesRegex(ValueError, "Plugin version"):
            info.validate_release(self.release, self.catalog_bytes, self.requirements, self.bumped()["plugin_version"])
        value = deepcopy(self.release)
        value["runtime_catalog"]["revision"] = True
        with self.assertRaisesRegex(ValueError, "revision"):
            info.validate_release(value, self.catalog_bytes, self.requirements, value["plugin_version"])

    def test_code_update_requires_version_bump_but_docs_only_does_not(self):
        changed = ["dlss5tool_comfy/core/color.py"]
        self.assertTrue(policy.update_errors(self.release, self.catalog, self.release, self.catalog, changed))
        self.assertFalse(policy.update_errors(self.release, self.catalog, self.bumped(), self.catalog, changed))
        self.assertFalse(policy.update_errors(self.release, self.catalog, self.release, self.catalog, ["README.md"]))

    def test_dependency_pin_change_alone_requires_version_bump(self):
        changed = deepcopy(self.release)
        changed["python_requirements"]["sha256"] = "0" * 64
        self.assertTrue(policy.update_errors(self.release, self.catalog, changed, self.catalog, []))
        changed["plugin_version"] = self.bumped()["plugin_version"]
        self.assertFalse(policy.update_errors(self.release, self.catalog, changed, self.catalog, []))

    def test_host_change_requires_component_and_catalog_revision(self):
        changed = deepcopy(self.catalog)
        changed["host"]["sha256"] = "0" * 64
        errors = policy.update_errors(self.release, self.catalog, self.bumped(), changed, [])
        self.assertTrue(any("catalog_revision" in e for e in errors))
        self.assertTrue(any("Host" in e for e in errors))
        changed["catalog_revision"] += 1
        changed["host"]["revision"] += 1
        self.assertFalse(policy.update_errors(self.release, self.catalog, self.bumped(), changed, []))

    def test_nr_change_requires_component_revision_and_keeps_other_profiles(self):
        changed = deepcopy(self.catalog)
        changed["catalog_revision"] += 1
        changed["profiles"][1]["dll_sha256"] = "0" * 64
        errors = policy.update_errors(self.release, self.catalog, self.bumped(), changed, [])
        self.assertTrue(any("NR ada" in e for e in errors))
        changed["profiles"][1]["revision"] += 1
        self.assertFalse(policy.update_errors(self.release, self.catalog, self.bumped(), changed, []))
        self.assertEqual(changed["profiles"][0], self.catalog["profiles"][0])

    def test_report_is_offline_and_contains_no_private_paths(self):
        with patch.object(runtime_setup, "verified_runtime", return_value=None), \
                patch.object(runtime_setup, "urlopen", side_effect=AssertionError("version query must be offline")):
            report = info.version_report()
        self.assertTrue(report["host"]["verified"])
        self.assertEqual(report["catalog_revision"], self.catalog["catalog_revision"])
        self.assertTrue(all(p["cache_status"] == "not_downloaded" for p in report["runtimes"]))
        text = json.dumps(report)
        self.assertNotIn(str(info.ROOT), text)
        self.assertNotIn("path", text)
        self.assertNotIn("@", text)

    def test_tag_must_match_installable_version(self):
        with self.assertRaisesRegex(ValueError, "Git tag"):
            policy.check(ref="refs/tags/v" + info.__version__ + ".mismatch")

    def test_real_git_baseline_detects_forgotten_version_bump(self):
        task = checked_path(info.ROOT / "tmp" / ("versioning-" + date.today().strftime("%Y%m%d")),
                            root=info.ROOT, exists=False)
        task.mkdir(parents=True, exist_ok=True)
        registration = checked_path(task / "TASK.md", root=task, exists=False)
        if not registration.exists():
            registration.write_text(f"# Version tests\n\nOwner: test_versioning.py. Created: {date.today()}. Review: {date.today() + timedelta(days=7)}.\n"
                                    "Purpose: synthetic Git version baseline. Rebuild: python -B -m unittest discover -s tests -p test_versioning.py.\n"
                                    "Budget below 1 MiB per test; per-test files are removed.\n", encoding="utf-8")
        root = checked_path(task / uuid.uuid4().hex, root=task, exists=False)
        root.mkdir()

        def git(*args):
            return subprocess.check_output(["git", "-c", f"safe.directory={root}", *args], cwd=root, stderr=subprocess.STDOUT).decode().strip()

        def write(name, data):
            path = checked_path(root / name, root=root, exists=False)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data, encoding="utf-8", newline="\n")

        try:
            git("init", "--initial-branch=main")
            git("config", "user.name", "Version tests")
            git("config", "user.email", "tests@users.noreply.github.com")
            write("version.json", json.dumps(self.release))
            write(info.CATALOG_PATH, json.dumps(self.catalog))
            write("dlss5tool_comfy/__init__.py", '__version__ = "1.2.3-beta.1"\n')
            write("dlss5tool_comfy/core/filter.py", "value = 1\n")
            write("CHANGELOG.md", "## v1.2.3-beta.1 test\n## v1.2.3-beta.2 test\n")
            git("add", "--", "version.json", "dlss5tool_comfy", "CHANGELOG.md")
            git("commit", "-m", "Synthetic baseline")
            base = git("rev-parse", "HEAD")
            write("dlss5tool_comfy/core/filter.py", "value = 2\n")
            with patch.object(policy, "ROOT", root), patch.object(policy, "load_release", return_value=(self.release, self.catalog)):
                _, errors = policy.check(base)
                self.assertTrue(any("increase the plugin version" in e for e in errors))
            with patch.object(policy, "ROOT", root), patch.object(policy, "load_release", return_value=(self.bumped(), self.catalog)):
                _, errors = policy.check(base)
                self.assertFalse(errors)
        finally:
            for path in root.rglob("*"):
                checked_path(path, root=root)
            checked_path(root, root=task)

            def remove_readonly(function, name, exception):
                path = checked_path(name, root=root)
                if not isinstance(exception[1], PermissionError):
                    raise exception[1]
                path.chmod(stat.S_IRUSR | stat.S_IWUSR)
                function(name)

            shutil.rmtree(root, onerror=remove_readonly)


if __name__ == "__main__":
    unittest.main()
