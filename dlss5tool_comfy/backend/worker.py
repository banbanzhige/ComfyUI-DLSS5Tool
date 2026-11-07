"""Disposable native host process. JSON control; owned shared-memory frames.

Only this process loads NGX. Parent termination also releases the D3D device
when native code is blocked or crashes. It never imports ComfyUI or Torch.
"""

import ctypes
import json
from multiprocessing import shared_memory
import os
from pathlib import Path
import sys
import threading

import numpy as np

# Launched by absolute filename so a ComfyUI package name may contain hyphens.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dlss5tool_comfy.backend.adapters import collect, _NativeAdapterInfo
from dlss5tool_comfy.backend.paths import identify
from dlss5tool_comfy.core.color import PROFILES


def watch_parent():
    """A killed ComfyUI must not leave a blocked native worker holding VRAM."""
    parent = os.environ.get("DLSS5TOOL_PARENT_PID")
    if os.name != "nt" or not parent:
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = bind(kernel, "OpenProcess", [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32], ctypes.c_void_p)
    handle = open_process(0x00100000, 0, int(parent))  # SYNCHRONIZE only
    if not handle:
        os._exit(1)
    wait = bind(kernel, "WaitForSingleObject", [ctypes.c_void_p, ctypes.c_uint32], ctypes.c_uint32)
    wait(handle, 0xFFFFFFFF)
    os._exit(1)


def bind(lib, name, args, result):
    fn = getattr(lib, name)
    fn.argtypes, fn.restype = args, result
    return fn


class NativeSession:
    def __init__(self, spec):
        self.lib = None
        self.memories = []
        self.input = self.output = None
        self.log_path = Path(spec["log_path"])
        for name in ("host", "runtime"):
            if identify(spec[name]["path"]).__dict__ != spec[name]:
                raise RuntimeError("DLL changed before native worker initialization")
        self.lib = ctypes.CDLL(spec["host"]["path"])
        lib = self.lib
        bind(lib, "dlssnr_shutdown", [], None)
        bind(lib, "dlssnr_configure", [ctypes.c_int] * 5, None)(1, 1, 1, 1, 0)
        tile_w, tile_h = spec["tile"]
        width, height = spec["width"], spec["height"]
        tiled = width > tile_w or height > tile_h
        bind(lib, "dlssnr_configure_tiling", [ctypes.c_int] * 3, None)(int(tiled), tile_w, tile_h)
        bind(lib, "dlssnr_configure_format", [ctypes.c_int] * 2, None)(1, PROFILES[spec["profile"]])
        bind(lib, "dlssnr_select_adapter", [ctypes.c_int32, ctypes.c_uint32], None)(
            spec["adapter"]["luid_high"], spec["adapter"]["luid_low"])
        options = spec["options"]
        bind(lib, "dlssnr_set_options", [ctypes.c_int, ctypes.c_int] + [ctypes.c_float] * 4
             + [ctypes.c_int] * 4 + [ctypes.c_float] * 2, None)(
            1, options["style"], options["intensity"], options["local_tone"],
            options["local_struct"], options["skin_struct"], int(options["use_auto_mask"]),
            0, 0, 2, 1.0, 1.0)
        initialized = bind(lib, "dlssnr_init", [ctypes.c_int] * 3 + [ctypes.c_wchar_p] * 2,
                           ctypes.c_int)(width, height, 1, spec["runtime"]["path"], str(self.log_path))
        if not initialized:
            raise RuntimeError("DLSSNR initialization failed")
        native = _NativeAdapterInfo()
        native.struct_size = ctypes.sizeof(native)
        if not bind(lib, "dlssnr_selected_adapter", [ctypes.POINTER(_NativeAdapterInfo)], ctypes.c_int)(ctypes.byref(native)):
            raise RuntimeError("Cannot verify the selected physical GPU")
        if (native.luid_high, native.luid_low) != (spec["adapter"]["luid_high"], spec["adapter"]["luid_low"]):
            raise RuntimeError("Native host selected a different GPU")
        if not bind(lib, "dlssnr_create_feature", [ctypes.c_int] * 3, ctypes.c_int)(width, height, 1):
            raise RuntimeError("DLSSNR Feature 18 creation failed")
        bind(lib, "dlssnr_process", [ctypes.c_void_p] * 4 + [ctypes.c_int], ctypes.c_int)
        shape = (height, width, 4)
        for name in ("input_name", "output_name"):
            # Windows mappings have kernel ownership and no resource-tracker
            # unlink on a non-owning child. track=False is new in Python 3.13.
            args = {"name": spec[name]}
            if sys.version_info >= (3, 13):
                args["track"] = False
            self.memories.append(shared_memory.SharedMemory(**args))
        self.input = np.ndarray(shape, np.float16, buffer=self.memories[0].buf)
        self.output = np.ndarray(shape, np.float16, buffer=self.memories[1].buf)
        self.metadata = {"adapter": spec["adapter"], "frame_format": "rgba16f",
                         "profile": spec["profile"], "tiled": tiled, "tile": [tile_w, tile_h]}

    def process(self):
        if not self.lib.dlssnr_process(self.input.ctypes.data, None, None, self.output.ctypes.data, 1):
            raise RuntimeError("DLSSNR evaluation failed")
        if not np.isfinite(self.output).all():
            raise RuntimeError("DLSSNR produced NaN or Inf")
        return self.metadata

    def close(self):
        if self.lib is not None:
            self.lib.dlssnr_shutdown()
        self.input = self.output = None
        for memory in self.memories:
            memory.close()
        self.memories.clear()


def main():
    threading.Thread(target=watch_parent, daemon=True).start()
    session = None
    try:
        for line in sys.stdin:
            try:
                message = json.loads(line)
                op = message["op"]
                if op == "inventory":
                    payload = collect(message["host_path"])
                elif op == "open":
                    if session is not None:
                        raise RuntimeError("A worker may own only one native session")
                    # Assign before initialization so partial native allocations
                    # are released even when feature creation fails.
                    session = NativeSession.__new__(NativeSession)
                    session.__init__(message["spec"])
                    payload = session.metadata
                elif op == "process" and session is not None:
                    payload = session.process()
                elif op == "close":
                    if session is not None:
                        session.close()
                        session = None
                    print(json.dumps({"ok": True, "payload": {}}), flush=True)
                    break
                else:
                    raise ValueError("Unknown worker command")
                reply = {"ok": True, "payload": payload}
            except Exception as error:
                log = ""
                if session is not None and session.log_path.is_file():
                    log = session.log_path.read_text(encoding="utf-8", errors="replace")[-2500:]
                reply = {"ok": False, "error": f"{type(error).__name__}: {error}", "log": log}
            print(json.dumps(reply, ensure_ascii=True), flush=True)
    finally:
        if session is not None:
            session.close()


if __name__ == "__main__":
    main()
