"""Launch the existing ComfyUI with isolated task state and this plugin source.

Does not install, replace or link files inside ComfyUI. Stops with Ctrl+C.
Only the current plugin is enabled; models and third-party custom nodes are
not loaded. For a normal installation use the documented installation layout.
"""

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dlss5tool_comfy.backend.paths import checked_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfy", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8199)
    args = parser.parse_args()
    comfy = checked_path(args.comfy)
    scratch = checked_path(args.scratch, root=ROOT / "tmp", exists=False)
    if not (scratch.parent / "TASK.md").is_file():
        raise ValueError("scratch must be under a registered task directory")
    scratch.mkdir(parents=True, exist_ok=True)
    for name in ("input", "output", "temp", "user", "custom_nodes"):
        checked_path(scratch / name, root=scratch, exists=False).mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(TEMP=str(scratch / "temp"), TMP=str(scratch / "temp"),
               PYTHONDONTWRITEBYTECODE="1", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
    command = [sys.executable, "-B", str(ROOT / "scripts/dev_server.py"),
               "--comfy", str(comfy), "--scratch", str(scratch), "--port", str(args.port)]
    log_path = checked_path(scratch / "server.log", root=scratch, exists=False)
    log_stream = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(command, cwd=str(ROOT), env=env, stdout=log_stream, stderr=subprocess.STDOUT,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    print(f"ComfyUI PID {process.pid}; log: {log_path}", flush=True)
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        return 0
    finally:
        log_stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
