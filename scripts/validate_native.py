"""Targeted real-GPU lifetime, still-image reset and automatic-tiling checks."""

import argparse
from dataclasses import asdict
import json
from multiprocessing import shared_memory
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from dlss5tool_comfy.backend.paths import checked_path, host_identity, identify
from dlss5tool_comfy.backend.process import ProcessLive, inventory
from dlss5tool_comfy.backend.runtime_setup import select_adapter, profile_for, ensure_runtime
from dlss5tool_comfy.core.contracts import ModelConfig

OPTIONS = dict(style=0, intensity=1., local_tone=1., local_struct=1., skin_struct=1., use_auto_mask=True)


def configuration():
    host = host_identity()
    choices = inventory(host)
    if not choices:
        raise RuntimeError("A compatible NVIDIA GPU is required")
    adapter = select_adapter(choices)
    return ModelConfig(ensure_runtime(profile_for(adapter)), host, adapter)


def released(names):
    for name in names:
        try:
            memory = shared_memory.SharedMemory(name=name)
        except FileNotFoundError:
            continue
        memory.close()
        raise AssertionError(f"Shared mapping still alive: {name}")


def run(scratch):
    scratch = checked_path(scratch, root=ROOT / "tmp", exists=False)
    if not (scratch.parent / "TASK.md").is_file():
        raise ValueError("Use a child directory of a registered task")
    scratch.mkdir(exist_ok=True)
    os.environ.update(TEMP=str(scratch), TMP=str(scratch))
    model = configuration()
    report = {"gpu": asdict(model.adapter), "runtime": asdict(model.runtime), "host": asdict(model.host), "cases": []}
    image = np.random.default_rng(42).uniform(.04, .96, (256, 320, 3)).astype(np.float32)

    def record(name, **values):
        case = {"name": name, "passed": True, **values}
        report["cases"].append(case)
        print(json.dumps(case), flush=True)

    fixture = checked_path(scratch / "identity-fixture.bin", root=scratch, exists=False)
    fixture.write_bytes(b"first content")
    before_stat = fixture.stat()
    identity = identify(fixture, pe=False)
    fixture.write_bytes(b"other content")
    os.utime(fixture, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))
    replaced = identify(fixture, pe=False)
    assert identity.size == replaced.size and identity.mtime_ns == replaced.mtime_ns
    assert identity.sha256 != replaced.sha256
    checked_path(fixture, root=scratch).unlink()
    record("same-path-size-timestamp-replacement-invalidates-identity")

    with ProcessLive(model, 320, 256, "srgb", OPTIONS, scratch) as live:
        pid = live.worker.process.pid
        names = [m.name for m in live.memories]
        first = live.process(image)
        live.process(image[::-1].copy())
        again = live.process(image)
        np.testing.assert_array_equal(first, again)
        preserved = first.copy()
        live.process(image * .7)
        np.testing.assert_array_equal(first, preserved)
    assert live.worker is None
    released(names)
    record("batch-reset-stable-owned-output", pid=pid)

    with ProcessLive(model, 320, 256, "hdr10_pq", OPTIONS, scratch) as live:
        process = live.worker.process
        names = [m.name for m in live.memories]
        process.kill()
        process.wait(timeout=5)
        try:
            live.process(image)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Native crash was treated as a success")
    released(names)
    record("worker-crash-released", exit_code=process.returncode)

    class Cancelled(Exception):
        pass

    cancellation = threading.Event()

    def cancel():
        if cancellation.is_set():
            raise Cancelled()

    live = ProcessLive(model, 320, 256, "srgb", OPTIONS, scratch, cancel)
    process = live.worker.process
    names = [m.name for m in live.memories]
    cancellation.set()
    start = time.monotonic()
    try:
        with live:
            live.process(image)
    except Cancelled:
        pass
    else:
        raise AssertionError("Cancellation was ignored")
    assert process.poll() is not None
    released(names)
    record("cancellation-released", seconds=round(time.monotonic() - start, 3))

    live = ProcessLive(model, 320, 256, "srgb", OPTIONS, scratch)
    process = live.worker.process
    names = [m.name for m in live.memories]
    live.input[..., :3], live.input[..., 3] = image, 1
    start = time.monotonic()
    try:
        with live:
            live.worker.request({"op": "process"}, timeout=.000001)
    except TimeoutError:
        pass
    else:
        raise AssertionError("Timeout did not fire")
    assert process.poll() is not None
    released(names)
    record("timeout-released", seconds=round(time.monotonic() - start, 3))

    # Exercise actual automatic native tiling, including a width not divisible
    # by the tile grid. The native source supplies 128px context + overlap.
    x = np.linspace(.05, .95, 2304, dtype=np.float32)
    tiled_input = np.broadcast_to(x[None, :, None], (512, 2304, 3)).copy()
    with ProcessLive(model, 2304, 512, "hdr10_pq", OPTIONS, scratch) as live:
        tiled = live.process(tiled_input)
        assert live.metadata["tiled"]
        assert tiled.shape == tiled_input.shape and np.isfinite(tiled).all()
        steps = np.abs(np.diff(tiled, axis=1))
        maximum_step = float(steps.max())
        # A smooth monochrome ramp must not acquire an abrupt stitching line.
        # This is a synthetic discontinuity check, not perceptual image QA.
        assert maximum_step < .025, maximum_step
        record("automatic-overlap-tiling", shape=list(tiled.shape), maximum_adjacent_step=maximum_step,
               strategy=live.metadata)

    with ProcessLive(model, 320, 256, "srgb", OPTIONS, scratch) as live:
        output = live.process(image)
        assert np.isfinite(output).all()
    record("reopen-after-failures")
    report["status"] = "passed"
    (scratch / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, required=True)
    arguments = parser.parse_args()
    run(arguments.scratch)
