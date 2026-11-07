"""Validate the actual ComfyUI executor without installing into custom_nodes.

Uses the supplied ComfyUI checkout and its interpreter. All generated input,
output, previews, user state and temporary files live in a registered task
directory. No other custom nodes, models, servers or network are required.
"""

import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dlss5tool_comfy.backend.paths import checked_path


def setup(comfy, scratch):
    comfy = checked_path(comfy)
    scratch = checked_path(scratch, root=ROOT / "tmp", exists=False)
    if not (scratch.parent / "TASK.md").is_file():
        raise ValueError("scratch must be a child of a registered tmp task")
    scratch.mkdir(parents=True, exist_ok=True)
    for name in ("input", "output", "temp", "user"):
        checked_path(scratch / name, root=scratch, exists=False).mkdir(exist_ok=True)
    os.environ.update(TEMP=str(scratch / "temp"), TMP=str(scratch / "temp"), PYTHONDONTWRITEBYTECODE="1")
    sys.path.insert(0, str(comfy))
    from comfy.cli_args import args
    args.base_directory = str(scratch)
    args.user_directory = str(scratch / "user")
    args.disable_metadata = False
    args.offline = True
    import folder_paths
    folder_paths.set_temp_directory(str(scratch / "temp"))
    folder_paths.set_input_directory(str(scratch / "input"))
    folder_paths.set_output_directory(str(scratch / "output"))
    import nodes

    async def load():
        for name in ("nodes_images.py", "nodes_mask.py"):
            if not await nodes.load_custom_node(str(comfy / "comfy_extras" / name), module_parent="comfy_extras"):
                raise RuntimeError(f"Could not load native {name}")
        if not await nodes.load_custom_node(str(ROOT)):
            raise RuntimeError("Could not load plugin")

    asyncio.run(load())
    return scratch, nodes


def graph(gpu, profile):
    from dlss5tool_comfy.core.contracts import RESIDUAL_FIELDS, QUALITY_FIELDS

    prompt = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "contract.png"}},
        "2": {"class_type": "DLSS5ToolModelLoader", "inputs": {
            "render_gpu": gpu, "dll_selection": "auto", "dll_path": ""}},
        "3": {"class_type": "DLSS5ToolEnhance", "inputs": {
            "model": ["2", 0], "image": ["1", 0], "alpha_mask": ["1", 1],
            "style": "default", "intensity": 1., "local_tone": 1., "local_struct": 1.,
            "use_auto_mask": True, "skin_struct": 1.}},
        "4": {"class_type": "DLSS5ToolAdjust", "inputs": {
            "result": ["3", 1], **{key: 1. for key in (*RESIDUAL_FIELDS, *QUALITY_FIELDS)},
            "output_mix": 1., "mask_feather": 0}},
        "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0], "filename_prefix": "sdr"}},
    }
    if profile != "srgb":
        prompt["6"] = {"class_type": "ImageSDRToHDR", "inputs": {
            "image": ["1", 0], "algorithm": "lottes", "algorithm.target_luminance": 1000.}}
        prompt["3"]["inputs"]["image"] = ["6", 0]
        save_source = ["4", 0]
        if profile in ("hdr10_pq", "scrgb"):
            prompt["7"] = {"class_type": "ImageColorSpace", "inputs": {
                "image": ["6", 0], "source": "HDR", "destination": "HDR PQ" if profile == "hdr10_pq" else "linear"}}
            prompt["3"]["inputs"]["image"] = ["7", 0]
        if profile == "hdr10_pq":
            prompt["8"] = {"class_type": "ImageColorSpace", "inputs": {
                "image": ["4", 0], "source": "HDR PQ", "destination": "linear"}}
            save_source = ["8", 0]
        prompt["5"] = {"class_type": "SaveImageAdvanced", "inputs": {
            "images": save_source, "filename_prefix": profile, "format": "exr",
            "format.bit_depth": "32-bit float", "format.input_color_space": "HDR" if profile == "hdr10_hlg" else "linear"}}
    return prompt


def run(comfy, scratch):
    scratch, nodes = setup(comfy, scratch)
    import numpy as np
    import torch
    import av
    from PIL import Image
    import execution
    from dlss5tool_comfy.core.color import decode
    from dlss5tool_comfy.nodes.model import adapter_choices

    # Synthetic non-private input includes highlight detail, colour and alpha.
    y, x = np.mgrid[0:256, 0:320].astype(np.float32)
    rgb = np.stack((x / 319, y / 255, .5 + .4 * np.sin(x / 9) * np.cos(y / 11)), -1)
    alpha = np.clip(x / 40, 0, 1)
    Image.fromarray(np.rint(np.concatenate((rgb, alpha[..., None]), -1) * 255).astype(np.uint8)).save(scratch / "input/contract.png")
    from server import PromptServer
    from app.assets.manager import NoAssets
    from comfy.cli_args import args

    # Real PromptServer initialization supplies the native save node's asset
    # context. Headless validation collects its events without binding a port.
    server = PromptServer(asyncio.new_event_loop(), asset_manager=NoAssets(args))
    server.events = []
    server.send_sync = lambda event, data, sid=None: server.events.append((event, data))
    executor = execution.PromptExecutor(server, cache_type=execution.CacheType.CLASSIC,
                                        cache_args={"ram": 16., "ram_inactive": 4.})
    gpu = adapter_choices()[0]
    report = {"environment": {"comfy": str(comfy), "torch": torch.__version__, "python": sys.version}, "cases": []}

    def execute(prompt, name):
        prompt_id = str(uuid.uuid4())
        validated = asyncio.run(execution.validate_prompt(prompt_id, prompt, None))
        if not validated[0]:
            raise RuntimeError(f"{name} graph invalid: {validated}")
        server.events.clear()
        executor.execute(prompt, prompt_id, {"client_id": "local-validation"}, validated[2])
        if not executor.success:
            errors = [data for event, data in server.events if event in ("execution_error", "execution_interrupted")]
            raise RuntimeError(f"{name} failed: {errors}")
        entry = asyncio.run(executor.caches.outputs.get("3"))
        result = entry.outputs[1][0]
        output = asyncio.run(executor.caches.outputs.get("4")).outputs[0][0]
        assert output.shape == result.source.shape
        assert tuple(output.shape[1:]) == (256, 320, 3)
        assert torch.isfinite(output).all()
        executed = [data.get("node") for event, data in server.events if event == "executing"]
        case = {"name": name, "profile": result.color.profile, "source": result.color.source,
                "shape": list(output.shape), "range": [float(output.min()), float(output.max())],
                "executed_nodes": executed, "preview_count": len(entry.ui["output"]["images"]),
                "alpha_range": [float(result.alpha_mask.min()), float(result.alpha_mask.max())]}
        saved = executor.history_result["outputs"]["5"]["images"][0]
        saved_path = checked_path(scratch / "output" / saved["subfolder"] / saved["filename"], root=scratch)
        if saved_path.suffix == ".exr":
            # Independent decode verifies the save path did not replace the
            # formal float output with its bounded SDR preview.
            save_link = prompt["5"]["inputs"]["images"]
            save_tensor = asyncio.run(executor.caches.outputs.get(str(save_link[0]))).outputs[save_link[1]][0]
            expected_saved = save_tensor[0].numpy()
            if prompt["5"]["inputs"]["format.input_color_space"] == "HDR":
                t = np.maximum(expected_saved, 0)
                expected_saved = np.where(t <= .5, t * t / 3,
                                          (np.exp((np.maximum(t, .5) - .55991072928) / .17883277) + .28466892) / 12)
            with av.open(str(saved_path)) as container:
                frame = next(container.decode(video=0))
                actual_saved = frame.to_ndarray(format="gbrpf32le")
            np.testing.assert_allclose(actual_saved, expected_saved, rtol=1e-5, atol=1e-6)
            case["saved_float_range"] = [float(actual_saved.min()), float(actual_saved.max())]
        report["cases"].append(case)
        print(json.dumps(case), flush=True)
        return result, output

    for profile in ("srgb", "hdr10_hlg", "hdr10_pq", "scrgb"):
        prompt = graph(gpu, profile)
        result, output = execute(prompt, profile)
        assert result.color.profile == profile
        assert torch.equal(output, result.rendered)
        if profile == "scrgb":
            assert output.max() > 1, "Native linear HDR output clipped super-white values"
        assert result.alpha_mask.min() == 0 and result.alpha_mask.max() == 1
        before = result
        changed = copy.deepcopy(prompt)
        changed["4"]["inputs"]["total"] = .5
        changed_result, adjusted = execute(changed, profile + "-adjust-cache")
        assert changed_result is before, "Adjust change reran Enhance"
        expected = decode(result.source.numpy(), result.color) + .5 * (
            decode(result.rendered.numpy(), result.color) - decode(result.source.numpy(), result.color)) if result.color.hdr else (
                result.source.numpy() + .5 * (result.rendered.numpy() - result.source.numpy()))
        actual = decode(adjusted.numpy(), result.color) if result.color.hdr else adjusted.numpy()
        np.testing.assert_allclose(actual, expected, rtol=3e-4, atol=2e-3 if result.color.hdr else 1e-6)
        # Core masking endpoints are CPU checked; here exercise the real ComfyUI
        # MASK link, arbitrary soft edge and full-frame feather execution.
        changed["9"] = {"class_type": "SolidMask", "inputs": {"value": 1., "width": 320, "height": 256}}
        changed["4"]["inputs"].update(protect_mask=["9", 0], mask_feather=2)
        masked_result, protected = execute(changed, profile + "-protect-cache")
        assert masked_result is before
        assert torch.equal(protected, result.source)
    # A real two-image batch, with alpha B=1 broadcast and independent still
    # resets in the same host. Both copies must produce identical output.
    batch_prompt = graph(gpu, "hdr10_hlg")
    batch_prompt["10"] = {"class_type": "RepeatImageBatch", "inputs": {"image": ["6", 0], "amount": 2}}
    batch_prompt["3"]["inputs"]["image"] = ["10", 0]
    batch_result, batch_output = execute(batch_prompt, "hdr-two-image-batch")
    assert batch_output.shape[0] == 2
    assert torch.equal(batch_output[0], batch_output[1])
    assert torch.equal(batch_result.alpha_mask[0], batch_result.alpha_mask[1])
    changed = copy.deepcopy(batch_prompt)
    changed["3"]["inputs"]["intensity"] = .8
    next_result, _ = execute(changed, "enhance-parameter-invalidates-cache")
    assert next_result is not batch_result
    changed["2"]["inputs"].update(dll_selection="custom", dll_path=next_result.backend.runtime.path)
    configured_result, _ = execute(changed, "loader-change-invalidates-enhance-cache")
    assert configured_result is not next_result
    mask_prompt = graph(gpu, "srgb")
    mask_prompt["6"] = {"class_type": "LoadImageMask", "inputs": {"image": "contract.png", "channel": "red"}}
    mask_prompt["4"]["inputs"].update(protect_mask=["6", 0], mask_feather=2)
    mask_result, mask_output = execute(mask_prompt, "native-image-mask-soft-edge-feather")
    assert torch.isfinite(mask_output).all()
    # Save exact executed API graphs for examples; UI representations are
    # exported from the native frontend, not synthesized as mock node cards.
    for name, prompt in (("basic-sdr", graph(gpu, "srgb")), ("masked-sdr", mask_prompt),
                         ("native-hlg", graph(gpu, "hdr10_hlg")),
                         ("native-pq", graph(gpu, "hdr10_pq")), ("native-linear", graph(gpu, "scrgb"))):
        (scratch / f"{name}.api.json").write_text(json.dumps(prompt, indent=2) + "\n", encoding="utf-8")
    report["status"] = "passed"
    report["limitations"] = ["Native frontend layout not visually inspected", "Synthetic input; perceptual comparison pending"]
    (scratch / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("PASS: real ComfyUI executor, SDR/HLG/PQ/linear, native saving, cache and protection", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfy", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    args = parser.parse_args()
    # ComfyUI's CLI must not consume this validation script's flags.
    sys.argv = [sys.argv[0]]
    run(args.comfy, args.scratch)


if __name__ == "__main__":
    main()
