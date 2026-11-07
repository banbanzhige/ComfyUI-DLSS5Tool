"""Stable data owned by the graph cache, never by a native execution session."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FileIdentity:
    path: str
    size: int
    mtime_ns: int
    ctime_ns: int
    file_id: int
    sha256: str


@dataclass(frozen=True)
class Adapter:
    id: str
    name: str
    memory: int
    luid_high: int
    luid_low: int
    compute_capability: int = 0


@dataclass(frozen=True)
class ModelConfig:
    runtime: FileIdentity
    host: FileIdentity
    adapter: Adapter
    abi: str = "dlssnr_host_v2/float-adapter-v1"


@dataclass(frozen=True)
class ColorInfo:
    profile: str = "srgb"
    source: str = "Unmarked ComfyUI IMAGE: sRGB"
    white_nits: float = 203.0
    peak_nits: float = 1000.0
    hlg_gamma: float = 1.2

    @property
    def hdr(self):
        return self.profile != "srgb"


@dataclass(frozen=True)
class ImageResult:
    # Tensors are detached CPU float32 snapshots. Node IMAGE outputs are separate
    # copies so downstream in-place edits cannot overwrite this cache entry.
    source: Any
    rendered: Any
    alpha_mask: Any
    color: ColorInfo
    backend: ModelConfig
    diagnostics: tuple


RESIDUAL_FIELDS = (
    "residualMultiplier", "shadowStructureMultiplier", "reflectionGlowMultiplier",
    "residualSaturation", "residualLightness",
)
QUALITY_FIELDS = ("total", "darken", "brighten", "color", "luminance")
STYLES = ("default", "natural", "cinematic")
