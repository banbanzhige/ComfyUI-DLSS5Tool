"""Native image UI payloads backed by a bounded plugin-owned temp directory."""

import hashlib
import math
import os
import re
import threading
import uuid

from ..backend.paths import checked_path
from ..core.color import sdr_preview


_lock = threading.Lock()
_PREVIEW_NAME = re.compile(r"[0-9a-f]{64}\.png\Z")
_LOG_NAME = re.compile(r"host-[0-9a-f]{32}\.log\Z")


def temp_directory():
    import folder_paths

    root = checked_path(folder_paths.get_temp_directory(), exists=False)
    root.mkdir(parents=True, exist_ok=True)
    directory = checked_path(root / "dlss5tool_comfy", root=root, exists=False)
    directory.mkdir(exist_ok=True)
    return directory


def trim(directory, pattern, keep):
    entries = [checked_path(p, root=directory) for p in directory.iterdir() if pattern.fullmatch(p.name)]
    for path in sorted(entries, key=lambda p: p.stat().st_mtime_ns, reverse=True)[keep:]:
        checked_path(path, root=directory).unlink()


def previews(images, color):
    import numpy as np
    import torch.nn.functional as functional
    from PIL import Image

    directory = checked_path(temp_directory() / "previews", exists=False)
    directory.mkdir(exist_ok=True)
    height, width = images.shape[1:3]
    ratio = min(1.0, 768 / max(height, width))
    small = functional.interpolate(images[:4].permute(0, 3, 1, 2),
                                   size=(max(1, math.floor(height * ratio)), max(1, math.floor(width * ratio))),
                                   mode="area").permute(0, 2, 3, 1).numpy()
    entries = []
    with _lock:
        for rgb in small:
            pixels = np.rint(sdr_preview(rgb, color) * 255).astype(np.uint8)
            name = hashlib.sha256(str(pixels.shape).encode() + pixels.tobytes()).hexdigest() + ".png"
            target = checked_path(directory / name, root=directory, exists=False)
            if not target.exists():
                staged = checked_path(directory / f"{uuid.uuid4().hex}.tmp", root=directory, exists=False)
                try:
                    Image.fromarray(pixels).save(staged, format="PNG")
                    checked_path(target, root=directory, exists=False)
                    os.replace(staged, target)
                finally:
                    if staged.exists():
                        checked_path(staged, root=directory).unlink()
            else:
                target.touch()
            entries.append({"filename": name, "subfolder": "dlss5tool_comfy/previews", "type": "temp"})
        trim(directory, _PREVIEW_NAME, 64)
        logs = checked_path(temp_directory() / "logs", exists=False)
        if logs.is_dir():
            trim(logs, _LOG_NAME, 16)
    return {"images": entries}
