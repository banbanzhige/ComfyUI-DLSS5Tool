"""The three stable node IDs; no GPU work during registration."""

from .model import ModelLoader
from .enhance import Enhance
from .adjust import Adjust

NODE_CLASS_MAPPINGS = {
    "DLSS5ToolModelLoader": ModelLoader,
    "DLSS5ToolEnhance": Enhance,
    "DLSS5ToolAdjust": Adjust,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "DLSS5ToolModelLoader": "DLSS5 Model Loader",
    "DLSS5ToolEnhance": "DLSS5 Enhance",
    "DLSS5ToolAdjust": "DLSS5 Adjust",
}
