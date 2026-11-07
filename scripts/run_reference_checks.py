from pathlib import Path
import os
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
reference = root / "reference/upstream"
env = dict(os.environ)
env["PYTHONPATH"] = str(reference)
env["PYTHONDONTWRITEBYTECODE"] = "1"
env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
# Keep pytest's package discovery inside the fixed reference tree. Otherwise
# the plugin's root registration __init__.py is imported as a test package.
raise SystemExit(subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "--tb=short",
    "-p", "no:cacheprovider", "--rootdir", str(reference), "--confcutdir", str(reference),
    "tests/test_host_queue.py", "tests/test_hdr_pipeline.py",
    "tests/test_sdr_composition_buffers.py"], cwd=reference, env=env).returncode)
