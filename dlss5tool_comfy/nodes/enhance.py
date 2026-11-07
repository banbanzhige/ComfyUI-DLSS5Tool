"""IMAGE adapter for same-resolution, per-still-image DLSSNR inference."""

import logging

from ..backend.process import ProcessLive
from ..core.composition import number
from ..core.color import native_input, native_output
from ..core.contracts import ImageResult, ModelConfig, STYLES
from ..core.masks import validate_images, validate_mask
from .color_source import infer_color, load_image_alpha_sentinel
from .preview import previews, temp_directory


def float_widget(default=1.0, minimum=0.0, maximum=5.0):
    return ("FLOAT", {"default": default, "min": minimum, "max": maximum, "step": .01, "display": "number"})


class Enhance:
    CATEGORY = "DLSS5"
    RETURN_TYPES = ("IMAGE", "DLSS5_RESULT", "MASK")
    RETURN_NAMES = ("image", "result", "alpha_mask")
    FUNCTION = "enhance"
    OUTPUT_NODE = True
    DESCRIPTION = "Same-size neural rendering, automatic tiling, native preview. Recognizes native HDR colour producers; unmarked IMAGE is sRGB."

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ("DLSS5_MODEL",), "image": ("IMAGE",),
            "style": (list(STYLES), {"default": "default"}),
            "intensity": float_widget(), "local_tone": float_widget(), "local_struct": float_widget(),
            "use_auto_mask": ("BOOLEAN", {"default": True}), "skin_struct": float_widget(),
        }, "optional": {"alpha_mask": ("MASK",), "color_info": ("DLSS5_COLOR_INFO",)},
            "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"}}

    def enhance(self, model, image, style="default", intensity=1., local_tone=1., local_struct=1.,
                use_auto_mask=True, skin_struct=1., alpha_mask=None, color_info=None, prompt=None, unique_id=None):
        import torch
        from comfy.model_management import throw_exception_if_processing_interrupted as cancel

        if not isinstance(model, ModelConfig):
            raise ValueError("Connect a DLSS5 Model Loader configuration")
        if style not in STYLES or type(use_auto_mask) is not bool:
            raise ValueError("Invalid Enhance style or automatic skin-mask flag")
        options = {"style": STYLES.index(style), "use_auto_mask": use_auto_mask}
        for name, value in (("intensity", intensity), ("local_tone", local_tone),
                            ("local_struct", local_struct), ("skin_struct", skin_struct)):
            options[name] = number(value, 0, 5, name)
        source = validate_images(image)
        alpha_mask = load_image_alpha_sentinel(prompt, unique_id, alpha_mask, source.shape)
        alpha = validate_mask(alpha_mask, source.shape, "alpha_mask")
        color = infer_color(prompt, unique_id, color_info)
        height, width = source.shape[1:3]
        frames = []
        native_profile = "hdr10_pq" if color.hdr else "srgb"
        with ProcessLive(model, width, height, native_profile, options, temp_directory() / "logs", cancel) as live:
            for frame in source.numpy():
                cancel()
                sent = native_input(frame, color)
                frames.append(torch.from_numpy(native_output(frame, sent, live.process(sent), color)))
            diagnostics = tuple(sorted((key, tuple(value) if isinstance(value, list) else str(value))
                                       for key, value in live.metadata.items()))
            logging.info("DLSS5 Enhance: %s; %s; %dx%d batch=%d", color.source, live.metadata, width, height, len(frames))
        rendered = torch.stack(frames)
        result = ImageResult(source, rendered, alpha, color, model, diagnostics)
        ui = previews(rendered, color)
        return {"ui": ui, "result": (rendered.clone(), result, alpha.clone())}
