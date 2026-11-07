"""Extracted adapter_inventory.py; provenance is in the implementation record.

Source: banbanzhige/DLSS5Tool; fixed handoff source/sha256 manifest.
Retain LICENSE and licenses/DLSS5Tool-original-MIT.txt with this module.
Desktop settings and optional native-composition paths are excluded.
"""

import ctypes
import os


_NVIDIA_VENDOR_ID = 0x10DE


_ADAPTER_SOFTWARE = 1


_ADAPTER_D3D12_LEVEL_11 = 2


class _NativeAdapterInfo(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("preference_index", ctypes.c_uint32),
        ("vendor_id", ctypes.c_uint32),
        ("device_id", ctypes.c_uint32),
        ("subsys_id", ctypes.c_uint32),
        ("revision", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("dedicated_video_memory", ctypes.c_uint64),
        ("luid_high", ctypes.c_int32),
        ("luid_low", ctypes.c_uint32),
        ("description", ctypes.c_wchar * 128),
    ]


def _cuda_adapter_luids():
    """Map physical CUDA devices to DXGI LUIDs without importing CUDA toolkits."""
    if os.name != "nt":
        return {}
    try:
        cuda = ctypes.WinDLL("nvcuda.dll")
        cuda.cuInit.argtypes = [ctypes.c_uint]
        cuda.cuInit.restype = ctypes.c_int
        if cuda.cuInit(0) != 0:
            return {}
        count_fn = getattr(cuda, "cuDeviceGetCount_v2", None) or cuda.cuDeviceGetCount
        count_fn.argtypes = [ctypes.POINTER(ctypes.c_int)]
        count_fn.restype = ctypes.c_int
        count = ctypes.c_int()
        if count_fn(ctypes.byref(count)) != 0 or count.value < 0 or count.value > 64:
            return {}
        cuda.cuDeviceGet.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
        cuda.cuDeviceGet.restype = ctypes.c_int
        cuda.cuDeviceGetLuid.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint), ctypes.c_int,
        ]
        cuda.cuDeviceGetLuid.restype = ctypes.c_int
        capability_fn = getattr(cuda, "cuDeviceComputeCapability", None)
        if capability_fn is not None:
            capability_fn.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.c_int]
            capability_fn.restype = ctypes.c_int
        bus_fn = getattr(cuda, "cuDeviceGetPCIBusId", None)
        if bus_fn is not None:
            bus_fn.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
            bus_fn.restype = ctypes.c_int
        result = {}
        for ordinal in range(count.value):
            device = ctypes.c_int()
            luid = (ctypes.c_ubyte * 8)()
            node_mask = ctypes.c_uint()
            if cuda.cuDeviceGet(ctypes.byref(device), ordinal) != 0:
                continue
            if cuda.cuDeviceGetLuid(luid, ctypes.byref(node_mask), device.value) != 0:
                continue
            luid_low = int.from_bytes(bytes(luid[:4]), "little", signed=False)
            luid_high = int.from_bytes(bytes(luid[4:]), "little", signed=True)
            pci_bus_id = ""
            capability = 0
            if capability_fn is not None:
                major, minor = ctypes.c_int(), ctypes.c_int()
                if capability_fn(ctypes.byref(major), ctypes.byref(minor), device.value) == 0:
                    capability = major.value * 10 + minor.value
            if bus_fn is not None:
                bus = ctypes.create_string_buffer(32)
                if bus_fn(bus, len(bus), device.value) == 0:
                    pci_bus_id = bus.value.decode("ascii", errors="replace")
            result[(luid_high, luid_low)] = {
                "cuda_index": ordinal,
                "pci_bus_id": pci_bus_id,
                "compute_capability": capability,
            }
        return result
    except (AttributeError, OSError, TypeError, ValueError):
        return {}


def _adapter_records(native_infos, cuda_luids=None):
    """Return stable, persistable NVIDIA D3D12 choices from native DXGI data."""
    records = []
    seen = set()
    cuda_luids = dict(cuda_luids or {})
    for native in native_infos:
        if (
            int(native.vendor_id) != _NVIDIA_VENDOR_ID
            or int(native.flags) & _ADAPTER_SOFTWARE
            or not int(native.flags) & _ADAPTER_D3D12_LEVEL_11
        ):
            continue
        base = "dxgi:%04X:%04X:%08X:%08X:%016X" % (
            int(native.vendor_id), int(native.device_id), int(native.subsys_id),
            int(native.revision), int(native.dedicated_video_memory),
        )
        luid = (int(native.luid_high), int(native.luid_low))
        cuda = cuda_luids.get(luid)
        if cuda_luids and cuda is None:
            continue
        pci_token = "".join(
            character for character in str((cuda or {}).get("pci_bus_id", "")).upper()
            if character in "0123456789ABCDEF"
        )
        suffix = (
            ":P" + pci_token if len(pci_token) == 9
            else ":C%d" % cuda["cuda_index"] if cuda else ""
        )
        adapter_id = base + suffix
        # Hybrid/partitioned Windows drivers can publish several DXGI LUIDs for
        # one physical adapter. CUDA's driver LUID identifies physical NVIDIA
        # devices; when unavailable, retain the first high-performance view.
        if adapter_id in seen:
            continue
        seen.add(adapter_id)
        records.append({
            "id": adapter_id,
            "name": str(native.description).strip() or "NVIDIA GPU",
            "preference_index": int(native.preference_index),
            "vendor_id": int(native.vendor_id),
            "device_id": int(native.device_id),
            "subsys_id": int(native.subsys_id),
            "revision": int(native.revision),
            "dedicated_video_memory": int(native.dedicated_video_memory),
            "luid_high": int(native.luid_high),
            "luid_low": int(native.luid_low),
            "d3d12_feature_level": "12_0" if int(native.flags) & 4 else "11_0",
            "cuda_index": cuda["cuda_index"] if cuda else None,
            "pci_bus_id": cuda["pci_bus_id"] if cuda else "",
            "compute_capability": cuda.get("compute_capability", 0) if cuda else 0,
        })
    return records


def _native_adapter_infos(lib):
    if not hasattr(lib, "dlssnr_enumerate_adapters"):
        return []
    count = int(lib.dlssnr_enumerate_adapters(None, 0))
    if count < 0 or count > 64:
        raise RuntimeError("DLSS 宿主返回了无效的显卡数量")
    if not count:
        return []
    items = (_NativeAdapterInfo * count)()
    for item in items:
        item.struct_size = ctypes.sizeof(_NativeAdapterInfo)
    returned = int(lib.dlssnr_enumerate_adapters(items, count))
    if returned < 0:
        raise RuntimeError("DLSS 宿主无法枚举图形适配器")
    return list(items[:min(returned, count)])


def collect(host_path):
    library = ctypes.CDLL(host_path)
    if not hasattr(library, "dlssnr_enumerate_adapters"):
        raise RuntimeError("当前 v2 宿主不支持显卡选择，请更新 dlssnr_host_v2.dll")
    library.dlssnr_enumerate_adapters.argtypes = [ctypes.POINTER(_NativeAdapterInfo), ctypes.c_uint32]
    library.dlssnr_enumerate_adapters.restype = ctypes.c_int
    native = _native_adapter_infos(library)
    cuda = _cuda_adapter_luids() if any(int(r.vendor_id) == _NVIDIA_VENDOR_ID for r in native) else {}
    return dict(adapters=_adapter_records(native, cuda), cuda_identity_available=bool(cuda),
                warnings=[] if cuda or not native else ["CUDA 显卡身份查询不可用，采用 DXGI 枚举信息"])
