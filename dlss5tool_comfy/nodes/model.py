"""Configuration-only model loader, with isolated physical-GPU discovery."""

import threading
import time
import logging

from ..backend.paths import host_identity, runtime_choices, runtime_identity
from ..backend.process import inventory
from ..core.contracts import ModelConfig
from ..backend.runtime_setup import select_adapter, profile_for, runtime_fingerprint, ensure_runtime


_lock = threading.Lock()
_cache = None


def adapters(refresh=False):
    global _cache
    host = host_identity()
    with _lock:
        if not refresh and _cache and _cache[0] == host and time.monotonic() - _cache[1] < 30:
            return _cache[2]
        values = inventory(host)
        _cache = host, time.monotonic(), values
        return values


def adapter_choices():
    try:
        values = adapters()
        return ["auto"] + [f"{a.name} | {a.id}" for a in values]
    except (OSError, RuntimeError, ValueError, TimeoutError):
        return ["auto"]


class ModelLoader:
    CATEGORY = "DLSS5"
    RETURN_TYPES = ("DLSS5_MODEL",)
    RETURN_NAMES = ("model",)
    FUNCTION = "load"
    DESCRIPTION = "Automatically match a physical NVIDIA GPU and a verified DLSSNR runtime. First use downloads its pinned runtime. Manual selection remains available."

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "render_gpu": (adapter_choices(), {"default": "auto"}),
            "dll_selection": (runtime_choices(), {"default": "auto"}),
            "dll_path": ("STRING", {"default": "", "tooltip": "Used when dll_selection is custom"}),
        }}

    @classmethod
    def IS_CHANGED(cls, render_gpu="auto", dll_selection="auto", dll_path=""):
        try:
            # File content identity, including same-path replacement, belongs
            # in the graph fingerprint rather than in a long-lived session.
            adapter = select_adapter(adapters(), render_gpu)
            if dll_selection == "auto":
                profile = profile_for(adapter)
                return repr((adapter, profile, runtime_fingerprint(profile), host_identity()))
            return repr((adapter, runtime_identity(dll_selection, dll_path), host_identity()))
        except (OSError, ValueError, RuntimeError):
            return float("nan")

    def load(self, render_gpu="auto", dll_selection="auto", dll_path=""):
        host = host_identity()
        adapter = select_adapter(adapters(refresh=True), render_gpu)
        if dll_selection == "auto":
            from comfy.model_management import throw_exception_if_processing_interrupted as cancel
            from comfy.utils import ProgressBar
            progress = ProgressBar(100)
            runtime = ensure_runtime(profile_for(adapter), cancel,
                                     lambda current, total: progress.update_absolute(current * 100 // total, 100))
        else:
            runtime = runtime_identity(dll_selection, dll_path)
        logging.info("DLSS5 Loader: %s, SM %s; runtime %s (%s)", adapter.name,
                     adapter.compute_capability, runtime.path, runtime.sha256)
        return (ModelConfig(runtime, host, adapter),)
