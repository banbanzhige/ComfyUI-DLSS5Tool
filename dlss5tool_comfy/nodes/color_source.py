"""Bounded adapters for explicit colour contracts in the real ComfyUI graph."""

from ..core.color import COMFY_SPACES, validate_color
from ..core.contracts import ColorInfo


PASSTHROUGH = {
    "ImageScale": "image", "ImageScaleBy": "image", "ImageCrop": "image",
    "ImageFlip": "image", "ImageRotate": "image", "ImageScaleToMaxDimension": "image",
    "ImageFromBatch": "image", "RepeatImageBatch": "image",
}


def linked_node(prompt, node_id, input_name):
    node = (prompt or {}).get(str(node_id), {})
    link = node.get("inputs", {}).get(input_name)
    if isinstance(link, (list, tuple)) and len(link) == 2 and isinstance(link[1], int):
        return str(link[0]), link[1], (prompt or {}).get(str(link[0]), {})
    return None


def infer_color(prompt, unique_id, explicit=None):
    if explicit is not None:
        return validate_color(explicit)
    current = linked_node(prompt, unique_id, "image")
    visited = set()
    while current is not None:
        node_id, slot, node = current
        if node_id in visited or len(visited) >= 64:
            raise ValueError("Colour source graph is cyclic or too deep; provide color_info")
        visited.add(node_id)
        kind = node.get("class_type")
        if kind == "ImageSDRToHDR" and slot == 0:
            return ColorInfo("hdr10_hlg", "ComfyUI ImageSDRToHDR: Rec.2020 HLG / 1000 nits")
        if kind == "ImageColorSpace" and slot == 0:
            space = node.get("inputs", {}).get("destination", "sRGB")
            if not isinstance(space, str) or space not in COMFY_SPACES:
                raise ValueError("Connect a supported ImageColorSpace destination (sRGB, HDR, HDR PQ, linear) or provide color_info")
            return ColorInfo(COMFY_SPACES[space], f"ComfyUI ImageColorSpace destination={space}")
        if kind in PASSTHROUGH and slot == 0:
            current = linked_node(prompt, node_id, PASSTHROUGH[kind])
            continue
        # An unknown transform after a known HDR producer must not silently
        # turn it into unmarked sRGB. Its actual colour behaviour is unknown.
        pending, scanned = [node], set()
        while pending:
            candidate = pending.pop()
            if len(scanned) >= 64:
                raise ValueError("Unresolved colour ancestry; provide color_info")
            for value in candidate.get("inputs", {}).values():
                if isinstance(value, (list, tuple)) and len(value) == 2 and isinstance(value[1], int):
                    key = str(value[0])
                    if key in scanned:
                        continue
                    scanned.add(key)
                    upstream = (prompt or {}).get(key, {})
                    if upstream.get("class_type") in ("ImageSDRToHDR", "ImageColorSpace"):
                        raise ValueError("A colour-producing node is behind an unsupported transform; connect it directly or supply color_info")
                    pending.append(upstream)
        break
    return ColorInfo()


def load_image_alpha_sentinel(prompt, unique_id, alpha, image_shape):
    # ComfyUI LoadImage uses a 64x64 zero MASK to mean no alpha, independently
    # of the image size. Recognize only that documented producer/output case.
    link = linked_node(prompt, unique_id, "alpha_mask")
    if (link is not None and link[1] == 1
            and link[2].get("class_type") in ("LoadImage", "LoadImageOutput")
            and getattr(alpha, "ndim", None) == 3 and tuple(alpha.shape[1:]) == (64, 64)
            and alpha.shape[0] in (1, image_shape[0]) and bool((alpha == 0).all())):
        return None
    return alpha
