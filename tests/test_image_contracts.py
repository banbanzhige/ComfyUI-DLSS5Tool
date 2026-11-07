from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import torch

from dlss5tool_comfy.core.color import decode, encode, sdr_preview, validate_color, native_input, native_output
from dlss5tool_comfy.core.composition import compose
from dlss5tool_comfy.core.contracts import ColorInfo, ImageResult, RESIDUAL_FIELDS, QUALITY_FIELDS
from dlss5tool_comfy.core.masks import validate_images, validate_mask, feather
from dlss5tool_comfy.nodes.color_source import infer_color, load_image_alpha_sentinel
from dlss5tool_comfy.nodes import NODE_CLASS_MAPPINGS


def packet(profile="srgb", batch=2):
    source = torch.linspace(.1, .65, batch * 7 * 9 * 3).reshape(batch, 7, 9, 3)
    rendered = source * 1.12
    if profile == "scrgb":
        source = source * 10 - 2
        rendered = source * 1.2 + .1
    return ImageResult(source, rendered, torch.rand(batch, 7, 9), ColorInfo(profile), None, ())


class ImageContracts(unittest.TestCase):
    def test_image_shape_dtype_and_finite(self):
        for value in (torch.zeros(1, 2, 2), torch.zeros(1, 2, 2, 4), torch.zeros(0, 2, 2, 3),
                      torch.zeros(1, 2, 2, 3, dtype=torch.uint8), torch.full((1, 2, 2, 3), float("nan"))):
            with self.subTest(shape=value.shape), self.assertRaises(ValueError):
                validate_images(value)

    def test_image_snapshot_does_not_alias_input(self):
        original = torch.rand(1, 2, 3, 3)
        result = validate_images(original)
        original.zero_()
        self.assertGreater(float(result.max()), 0)
        self.assertEqual(result.dtype, torch.float32)

    def test_mask_broadcast_clamp_and_nonalias(self):
        original = torch.tensor([[[-1., .5, 2.]]])
        actual = validate_mask(original, (3, 1, 3, 3), "protect_mask")
        original.zero_()
        torch.testing.assert_close(actual, torch.tensor([[[0., .5, 1.]]] * 3))

    def test_mask_invalid_dimensions_and_nonfinite(self):
        for mask in (torch.ones(7, 9), torch.ones(3, 7, 9), torch.ones(2, 9, 7),
                     torch.full((1, 7, 9), float("inf"))):
            with self.subTest(shape=mask.shape), self.assertRaises(ValueError):
                validate_mask(mask, (2, 7, 9, 3), "protect_mask")

    def test_feather_zero_preserves_soft_edges(self):
        mask = np.linspace(0, 1, 63, dtype=np.float32).reshape(1, 7, 9)
        np.testing.assert_array_equal(feather(mask, 0), mask)

    def test_feather_blends_both_sides_and_preserves_constants(self):
        mask = np.zeros((2, 17, 21), np.float32)
        mask[0, :, 10:] = 1
        mask[1] = 1
        actual = feather(mask, 2)
        self.assertGreater(actual[0, 8, 9], 0)
        self.assertLess(actual[0, 8, 10], 1)
        np.testing.assert_allclose(actual[1], 1, atol=1e-7)
        np.testing.assert_array_equal(feather(np.zeros_like(mask), 1), 0)

    def test_immutable_packet_fields(self):
        result = packet()
        with self.assertRaises(FrozenInstanceError):
            result.color = ColorInfo("scrgb")


class ColourContracts(unittest.TestCase):
    def test_reference_white_and_peaks(self):
        np.testing.assert_allclose(decode(np.ones((1, 3)), ColorInfo("hdr10_pq")), 10000, rtol=2e-6)
        np.testing.assert_allclose(decode(np.ones((1, 3)), ColorInfo("hdr10_hlg")), 1000, rtol=2e-6)
        np.testing.assert_allclose(decode(np.ones((1, 3)), ColorInfo("scrgb")), 203)
        np.testing.assert_allclose(decode(np.ones((1, 3)), ColorInfo()), 1, rtol=2e-6)

    def test_transfer_roundtrip(self):
        rgb = np.linspace(.001, .99, 60, dtype=np.float32).reshape(4, 5, 3)
        for profile in ("srgb", "scrgb", "hdr10_pq", "hdr10_hlg"):
            with self.subTest(profile=profile):
                info = ColorInfo(profile)
                np.testing.assert_allclose(encode(decode(rgb, info), info), rgb, atol=2e-6, rtol=2e-6)

    def test_signed_extended_linear_roundtrip(self):
        rgb = np.array([[[-.25, 4., 12.]]], np.float32)
        info = ColorInfo("scrgb")
        np.testing.assert_allclose(encode(decode(rgb, info), info), rgb)

    def test_hdr_native_transport_preserves_signed_and_superwhite_at_zero_residual(self):
        source = np.array([[[-.1, 4., 12.], [2., 2., 2.]]], np.float32)
        info = ColorInfo("scrgb")
        sent = native_input(source, info)
        self.assertGreaterEqual(sent.min(), 0)
        self.assertLessEqual(sent.max(), 1)
        received = sent.astype(np.float16).astype(np.float32)
        actual = native_output(source, sent, received, info)
        np.testing.assert_array_equal(actual, source)

    def test_native_hdr_limits_fail_without_silent_exposure_rescale(self):
        with self.assertRaisesRegex(ValueError, "10000"):
            native_input(np.full((1, 1, 3), 100, np.float32), ColorInfo("scrgb"))

    def test_unmarked_out_of_range_srgb_fails_without_guessing_or_clipping(self):
        with self.assertRaisesRegex(ValueError, "trusted HDR"):
            native_input(np.array([[[-.1, .5, 2.]]], np.float32), ColorInfo())

    def test_preview_is_separate_and_bounded(self):
        rgb = np.array([[[-.1, 4., 12.]]], np.float32)
        before = rgb.copy()
        preview = sdr_preview(rgb, ColorInfo("scrgb"))
        np.testing.assert_array_equal(rgb, before)
        self.assertTrue(np.isfinite(preview).all())
        self.assertGreaterEqual(preview.min(), 0)
        self.assertLessEqual(preview.max(), 1)

    def test_invalid_colour_metadata(self):
        for value in ({"profile": "hdr10_pq"}, {"source": "explicit"},
                      {"profile": "unknown", "source": "explicit"},
                      {"profile": "scrgb", "source": "explicit", "white_nits": float("nan")},
                      {"profile": "scrgb", "source": "explicit", "guess": True}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_color(value)

    def test_actual_producer_contract_and_linear_white(self):
        for kind, inputs, expected in (("ImageSDRToHDR", {}, "hdr10_hlg"),
                                       ("ImageColorSpace", {"destination": "HDR PQ"}, "hdr10_pq"),
                                       ("ImageColorSpace", {"destination": "linear"}, "scrgb")):
            with self.subTest(kind=kind, inputs=inputs):
                graph = {"1": {"class_type": kind, "inputs": inputs}, "2": {"inputs": {"image": ["1", 0]}}}
                info = infer_color(graph, "2")
                self.assertEqual(info.profile, expected)
                self.assertEqual(info.white_nits, 203)

    def test_unmarked_image_has_no_hdr_guess(self):
        self.assertEqual(infer_color({}, "1").profile, "srgb")

    def test_unknown_transform_after_hdr_fails_clearly(self):
        graph = {"1": {"class_type": "ImageSDRToHDR"},
                 "2": {"class_type": "UnknownTransform", "inputs": {"image": ["1", 0]}},
                 "3": {"inputs": {"image": ["2", 0]}}}
        with self.assertRaisesRegex(ValueError, "unsupported transform"):
            infer_color(graph, "3")

    def test_known_geometry_preserves_source_colour(self):
        graph = {"1": {"class_type": "ImageSDRToHDR"},
                 "2": {"class_type": "ImageScale", "inputs": {"image": ["1", 0]}},
                 "3": {"inputs": {"image": ["2", 0]}}}
        self.assertEqual(infer_color(graph, "3").profile, "hdr10_hlg")

    def test_unsupported_space_and_cyclic_ancestry(self):
        for graph in ({"1": {"class_type": "ImageColorSpace", "inputs": {"destination": "HDR ACEScct"}},
                       "3": {"inputs": {"image": ["1", 0]}}},
                      {"1": {"class_type": "ImageScale", "inputs": {"image": ["1", 0]}},
                       "3": {"inputs": {"image": ["1", 0]}}}):
            with self.assertRaises(ValueError):
                infer_color(graph, "3")


class CompositionContracts(unittest.TestCase):
    def test_neutral_preserves_raw_rendered_and_owned_storage(self):
        for profile in ("srgb", "scrgb", "hdr10_pq", "hdr10_hlg"):
            with self.subTest(profile=profile):
                result = packet(profile)
                actual = compose(result, {})
                self.assertTrue(torch.equal(actual, result.rendered))
                self.assertNotEqual(actual.data_ptr(), result.rendered.data_ptr())

    def test_zero_effect_returns_source_exactly(self):
        for profile in ("srgb", "scrgb", "hdr10_pq", "hdr10_hlg"):
            for values in ({"total": 0}, {"output_mix": 0}, {"darken": 0, "brighten": 0},
                           {"color": 0, "luminance": 0}):
                with self.subTest(profile=profile, values=values):
                    result = packet(profile)
                    self.assertTrue(torch.equal(compose(result, values), result.source))

    def test_black_and_white_masks_are_exact(self):
        for profile in ("srgb", "scrgb", "hdr10_pq", "hdr10_hlg"):
            result = packet(profile)
            for radius in (0, 2):
                with self.subTest(profile=profile, radius=radius):
                    white = compose(result, {"mask_feather": radius}, torch.ones(1, 7, 9))
                    black = compose(result, {"mask_feather": radius}, torch.zeros(1, 7, 9))
                    self.assertTrue(torch.equal(white, result.source))
                    self.assertTrue(torch.equal(black, result.rendered))

    def test_hdr_gray_protection_blends_linear_light(self):
        for profile in ("scrgb", "hdr10_pq", "hdr10_hlg"):
            result = packet(profile)
            actual = compose(result, {}, torch.full((1, 7, 9), .5)).numpy()
            light = (decode(result.source.numpy(), result.color) + decode(result.rendered.numpy(), result.color)) / 2
            np.testing.assert_allclose(decode(actual, result.color), light, rtol=2e-5, atol=1e-4)

    def test_mix_applies_once(self):
        result = packet()
        actual = compose(result, {"output_mix": .5})
        torch.testing.assert_close(actual, (result.source + result.rendered) / 2)

    def test_adjust_does_not_touch_source_rendered_or_alpha(self):
        result = packet("hdr10_hlg")
        before = tuple(t.clone() for t in (result.source, result.rendered, result.alpha_mask))
        actual = compose(result, {"total": 1.3, "residualMultiplier": 1.4, "color": .7,
                                  "output_mix": 1.2}, torch.rand(1, 7, 9))
        self.assertTrue(torch.isfinite(actual).all())
        for original, snapshot in zip((result.source, result.rendered, result.alpha_mask), before):
            self.assertTrue(torch.equal(original, snapshot))

    def test_invalid_parameters_even_when_total_zero(self):
        for values in ({"total": 0, "color": float("nan")}, {"residualMultiplier": .5},
                       {"output_mix": 6}, {"mask_feather": 1.5}, {"extra": 1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                compose(packet(), values)

    def test_no_mask_ignores_feather(self):
        self.assertTrue(torch.equal(compose(packet(), {"mask_feather": 256}), packet().rendered))


class NodeContracts(unittest.TestCase):
    def test_workflow_contract_matches_real_schemas(self):
        contract = json.loads((Path(__file__).resolve().parents[1] / "workflows/node-contract.json").read_text(encoding="utf-8"))["nodes"]
        with patch("dlss5tool_comfy.nodes.model.adapter_choices", return_value=["test GPU"]), \
                patch("dlss5tool_comfy.nodes.model.runtime_choices", return_value=["test DLL", "custom"]):
            for name, cls in NODE_CLASS_MAPPINGS.items():
                schema = cls.INPUT_TYPES()
                actual = {key: "COMBO" if isinstance(value[0], list) else value[0]
                          for group in ("required", "optional") for key, value in schema.get(group, {}).items()}
                self.assertEqual(actual, contract[name]["inputs"])
                self.assertEqual(list(cls.RETURN_TYPES), contract[name]["outputs"])
                self.assertEqual(set(schema.get("optional", {})), set(contract[name].get("optional", [])))

    def test_exact_three_ids_and_native_number_widgets(self):
        self.assertEqual(set(NODE_CLASS_MAPPINGS), {"DLSS5ToolModelLoader", "DLSS5ToolEnhance", "DLSS5ToolAdjust"})
        for key in ("DLSS5ToolEnhance", "DLSS5ToolAdjust"):
            cls = NODE_CLASS_MAPPINGS[key]
            self.assertTrue(cls.OUTPUT_NODE)
            for name, descriptor in cls.INPUT_TYPES()["required"].items():
                if descriptor[0] in ("INT", "FLOAT"):
                    self.assertEqual(descriptor[1]["display"], "number")
        adjust = NODE_CLASS_MAPPINGS["DLSS5ToolAdjust"].INPUT_TYPES()
        self.assertEqual(adjust["required"]["mask_feather"][1]["default"], 0)
        self.assertEqual(set(adjust["required"]) - {"result", "output_mix", "mask_feather"},
                         {*RESIDUAL_FIELDS, *QUALITY_FIELDS})

    def test_load_image_alpha_sentinel_is_source_specific(self):
        graph = {"1": {"class_type": "LoadImage"}, "2": {"inputs": {"alpha_mask": ["1", 1]}}}
        alpha = torch.zeros(1, 64, 64)
        self.assertIsNone(load_image_alpha_sentinel(graph, "2", alpha, (1, 7, 9, 3)))
        self.assertIs(load_image_alpha_sentinel({}, "2", alpha, (1, 7, 9, 3)), alpha)
        alpha[0, 0, 0] = .1
        self.assertIs(load_image_alpha_sentinel(graph, "2", alpha, (1, 7, 9, 3)), alpha)

    def test_adjust_never_calls_native_process(self):
        with patch("dlss5tool_comfy.nodes.adjust.previews", return_value={"images": []}), \
                patch("dlss5tool_comfy.nodes.enhance.ProcessLive", side_effect=AssertionError("NGX must remain idle")), \
                patch.dict("sys.modules", {"comfy": type("Comfy", (), {})(),
                                          "comfy.model_management": type("MM", (), {"throw_exception_if_processing_interrupted": staticmethod(lambda: None)})()}):
            result = packet()
            output = NODE_CLASS_MAPPINGS["DLSS5ToolAdjust"]().adjust(result, total=.5)["result"]
            self.assertTrue(torch.equal(output[1], result.alpha_mask))


if __name__ == "__main__":
    unittest.main()
