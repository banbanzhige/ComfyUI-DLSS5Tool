"""ComfyUI registration; importing this module creates no GPU session."""

import logging

from .dlss5tool_comfy import __version__
from .dlss5tool_comfy.release_info import startup_identity

logging.info(startup_identity())

from .dlss5tool_comfy.nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "__version__"]
