"""CPU appearance controls reuse the stable Enhance output without NGX."""

from ..core.composition import compose
from ..core.contracts import ImageResult, RESIDUAL_FIELDS, QUALITY_FIELDS
from .enhance import float_widget
from .preview import previews


class Adjust:
    CATEGORY = "DLSS5"
    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "alpha_mask")
    FUNCTION = "adjust"
    OUTPUT_NODE = True
    DESCRIPTION = "Residual, quality and mix controls. White protect_mask preserves the source; black uses the adjusted image. Feather is Gaussian sigma in original-image pixels."

    @classmethod
    def INPUT_TYPES(cls):
        values = {"result": ("DLSS5_RESULT",)}
        for name in (*RESIDUAL_FIELDS, *QUALITY_FIELDS):
            values[name] = float_widget(minimum=1. if name == "residualMultiplier" else 0., maximum=2.)
        values["output_mix"] = float_widget()
        values["mask_feather"] = ("INT", {"default": 0, "min": 0, "max": 256, "step": 1, "display": "number"})
        return {"required": values, "optional": {"protect_mask": ("MASK",)}}

    def adjust(self, result, protect_mask=None, **options):
        from comfy.model_management import throw_exception_if_processing_interrupted as cancel

        if not isinstance(result, ImageResult):
            raise ValueError("Connect the result output of DLSS5 Enhance")
        image = compose(result, options, protect_mask, cancel)
        return {"ui": previews(image, result.color), "result": (image, result.alpha_mask.clone())}
