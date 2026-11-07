"""Local component discovery with no desktop settings or reference imports."""

import hashlib
import os
from pathlib import Path
import stat
import struct
import threading

from ..core.contracts import FileIdentity


ROOT = Path(__file__).resolve().parents[2]
_identity_cache = {}
_identity_lock = threading.Lock()
_exports_cache = {}


def checked_path(value, *, root=None, exists=True):
    path = Path(value).absolute()
    for entry in (path, *path.parents):
        try:
            info = entry.lstat()
        except FileNotFoundError:
            if exists and entry == path:
                raise
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 1024:
            raise ValueError(f"Reparse point rejected: {entry}")
    resolved = path.resolve(strict=exists)
    if root is not None and not resolved.is_relative_to(Path(root).resolve(strict=True)):
        raise ValueError(f"Path outside designated directory: {path}")
    return resolved


def identify(path, *, pe=True):
    path = checked_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Component is not a file: {path}")
    key = (str(path), info.st_size, info.st_mtime_ns, change_time(path, info), info.st_ino)
    cache_key = (pe, *key)
    with _identity_lock:
        cached = _identity_cache.get(cache_key)
        if cached is not None:
            return cached
        with path.open("rb") as stream:
            if pe:
                if stream.read(2) != b"MZ":
                    raise ValueError(f"Not a Windows DLL: {path}")
                stream.seek(60)
                offset = struct.unpack("<I", stream.read(4))[0]
                stream.seek(offset)
                if stream.read(6) != b"PE\0\0\x64\x86":
                    raise ValueError(f"An x64 Windows DLL is required: {path}")
                stream.seek(0)
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        after = path.stat()
        if (after.st_size, after.st_mtime_ns, change_time(path, after), after.st_ino) != key[1:]:
            raise RuntimeError("DLL changed while its identity was being read; retry")
        result = FileIdentity(*key, digest)
        if len(_identity_cache) >= 64:
            _identity_cache.clear()
        _identity_cache[cache_key] = result
        return result


def change_time(path, info):
    """NTFS ChangeTime catches same-size edits with a restored LastWriteTime.

    Python's Windows st_ctime may still mean creation time. It is insufficient
    for invalidating a DLL replaced in-place while preserving its timestamp.
    """
    if os.name != "nt":
        return info.st_ctime_ns
    import ctypes

    class BasicInfo(ctypes.Structure):
        _fields_ = [("creation", ctypes.c_int64), ("access", ctypes.c_int64),
                    ("write", ctypes.c_int64), ("change", ctypes.c_int64),
                    ("attributes", ctypes.c_uint32)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                   ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.GetFileInformationByHandleEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    kernel.GetFileInformationByHandleEx.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(str(path), 0x80, 7, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        basic = BasicInfo()
        if not kernel.GetFileInformationByHandleEx(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (basic.change - 116444736000000000) * 100
    finally:
        kernel.CloseHandle(handle)


def runtime_directory():
    return checked_path(os.environ.get("DLSS5TOOL_RUNTIME_DIR", ROOT / "runtime"))


def host_identity():
    # The host ships with the plugin; an external NR cache need not contain it.
    identity = identify(ROOT / "runtime" / "dlssnr_host_v2.dll")
    from ..release_info import load_release
    _, catalog = load_release(ROOT)
    spec = catalog["host"]
    if (identity.size, identity.sha256) != (spec["size"], spec["sha256"]):
        raise ValueError("Bundled v2 host failed verification; reinstall the plugin or update its host catalog")
    require_exports(identity, {"dlssnr_configure_format", "dlssnr_configure_tiling", "dlssnr_enumerate_adapters",
                               "dlssnr_select_adapter", "dlssnr_selected_adapter", "dlssnr_init",
                               "dlssnr_create_feature", "dlssnr_process", "dlssnr_shutdown"})
    return identity


def runtime_choices():
    try:
        directory = runtime_directory()
        return ["auto"] + sorted(p.name for p in directory.glob("nvngx_dlssnr*.dll")
                      if p.is_file() and not p.is_symlink()) + ["custom"]
    except (OSError, ValueError):
        return ["auto", "custom"]


def runtime_identity(selection, custom_path=""):
    if selection == "custom":
        if not isinstance(custom_path, str) or not custom_path.strip():
            raise ValueError("Set dll_path to an existing DLSSNR x64 DLL")
        identity = identify(custom_path.strip())
    else:
        if selection not in runtime_choices() or Path(selection).name != selection:
            raise ValueError("Selected DLSSNR DLL is unavailable")
        identity = identify(runtime_directory() / selection)
    require_exports(identity, {"NVSDK_NGX_D3D12_Init_Ext", "NVSDK_NGX_D3D12_CreateFeature",
                               "NVSDK_NGX_D3D12_EvaluateFeature", "NVSDK_NGX_D3D12_ReleaseFeature"})
    return identity


def verify_identity(identity):
    current = identify(identity.path)
    if current != identity:
        raise RuntimeError("DLL was replaced after Model Loader ran; execute Model Loader again")


def require_exports(identity, required):
    """Validate the ABI without loading a third-party DLL into ComfyUI."""
    with _identity_lock:
        names = _exports_cache.get(identity)
        if names is None:
            path = checked_path(identity.path)
            try:
                with path.open("rb") as stream:
                    def u32(offset):
                        stream.seek(offset)
                        return struct.unpack("<I", stream.read(4))[0]

                    pe_offset = u32(60)
                    stream.seek(pe_offset + 6)
                    section_count = struct.unpack("<H", stream.read(2))[0]
                    stream.seek(pe_offset + 20)
                    optional_size = struct.unpack("<H", stream.read(2))[0]
                    optional = pe_offset + 24
                    stream.seek(optional)
                    if stream.read(2) != b"\x0b\x02" or not 1 <= section_count <= 96:
                        raise ValueError("Invalid PE32+ headers")
                    export_rva = u32(optional + 112)
                    sections = []
                    for i in range(section_count):
                        stream.seek(optional + optional_size + i * 40 + 8)
                        virtual_size, virtual_address, raw_size, raw_address = struct.unpack("<4I", stream.read(16))
                        sections.append((virtual_address, max(virtual_size, raw_size), raw_address, raw_size))

                    def offset(rva):
                        for base, span, raw, raw_size in sections:
                            if base <= rva < base + span and rva - base < raw_size:
                                result = raw + rva - base
                                if result < identity.size:
                                    return result
                        raise ValueError("Invalid PE export address")

                    export = offset(export_rva)
                    count = u32(export + 24)
                    if not 1 <= count <= 16384:
                        raise ValueError("Invalid PE export count")
                    pointers = offset(u32(export + 32))
                    names = set()
                    for i in range(count):
                        stream.seek(offset(u32(pointers + i * 4)))
                        name = stream.read(256).split(b"\0", 1)[0]
                        names.add(name.decode("ascii"))
            except (struct.error, UnicodeError, ValueError) as error:
                raise ValueError(f"Cannot inspect x64 DLL exports: {path}") from error
            if len(_exports_cache) >= 64:
                _exports_cache.clear()
            _exports_cache[identity] = names
        missing = required - names
        if missing:
            raise ValueError(f"Incompatible DLL {Path(identity.path).name}; missing exports: {', '.join(sorted(missing))}")
