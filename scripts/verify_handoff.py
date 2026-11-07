from pathlib import Path
import argparse
import ast
import hashlib
import json
import stat

def check(root, strict=False):
    root = root.resolve()
    source = json.loads((root / "docs/provenance/SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
    if strict:
        # The initial transfer log is local maintenance material. Its original
        # bytes are preserved; a public checkout does not need it.
        manifest = json.loads((root / "HANDOFF_MANIFEST.json").read_text(encoding="utf-8"))
    else:
        manifest = {"files": [item for item in source["copied_files"]
                              if item["path"].startswith("reference/upstream/")]}
    checked = 0
    for item in manifest["files"]:
        if not strict and not item["path"].startswith("reference/upstream/"):
            continue
        path = root / item["path"]
        if not path.resolve().is_relative_to(root):
            raise ValueError("Escaped root: " + item["path"])
        for parent in (path, *path.parents):
            attrs = getattr(parent.lstat(), "st_file_attributes", 0)
            if parent.is_symlink() or attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
                raise ValueError("Link rejected: " + str(parent))
            if parent == root:
                break
        data = path.read_bytes()
        if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("Content mismatch: " + item["path"])
        if path.suffix == ".py":
            ast.parse(data.decode("utf-8-sig"), filename=item["path"])
        checked += 1
    for item in source["copied_files"]:
        if not strict and not item["path"].startswith("reference/upstream/"):
            continue
        if hashlib.sha256((root / item["path"]).read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Upstream source changed: " + item["path"])
    package = root / "reference/upstream/dlss5tool"
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            names = []
            if isinstance(node, ast.ImportFrom):
                if node.module == "dlss5tool":
                    names = [alias.name for alias in node.names]
                elif node.module and node.module.startswith("dlss5tool."):
                    names = [node.module[10:]]
            elif isinstance(node, ast.Import):
                names = [a.name[10:] for a in node.names if a.name.startswith("dlss5tool.")]
            for name in names:
                if not (package / (name.replace(".", "/") + ".py")).is_file():
                    raise ValueError("Missing local import: " + name)
    contract = json.loads((root / "workflows/node-contract.json").read_text(encoding="utf-8"))["nodes"]
    workflow_paths = [path for directory in ("planned", "examples")
                      for path in (root / "workflows" / directory).glob("*.api.json")]
    for path in sorted(workflow_paths):
        prompt = json.loads(path.read_text(encoding="utf-8"))
        for node in prompt.values():
            spec = contract.get(node["class_type"])
            if spec and (set(node["inputs"]) - set(spec["inputs"])):
                raise ValueError("Unknown node inputs: " + str(path))
            if spec and (set(spec["inputs"]) - set(spec.get("optional", [])) - set(node["inputs"])):
                raise ValueError("Missing node inputs: " + str(path))
            for value in node["inputs"].values():
                if isinstance(value, list):
                    if len(value) != 2 or str(value[0]) not in prompt or not isinstance(value[1], int):
                        raise ValueError("Invalid API connection: " + str(path))
        ui = json.loads(path.with_name(path.name.replace(".api.json", ".json")).read_text(encoding="utf-8"))
        indexed = {n["id"]: n for n in ui["nodes"]}
        for link_id, origin, slot, target, input_slot, kind in ui["links"]:
            out = indexed[origin]["outputs"][slot]
            incoming = indexed[target]["inputs"][input_slot]
            if out["type"] != kind or incoming["type"] != kind or incoming["link"] != link_id or link_id not in out["links"]:
                raise ValueError("Invalid UI connection: " + str(path))
        for key, node in prompt.items():
            if indexed[int(key)]["type"] != node["class_type"]:
                raise ValueError("UI/API node mismatch")
    forbidden = [f["path"] for f in manifest["files"] if Path(f["path"]).suffix.lower() in
        (".dll", ".exe", ".pt", ".pth", ".safetensors", ".zip", ".7z")]
    if forbidden:
        raise ValueError("Unexpected binary/model/package: " + repr(forbidden))
    print("PASS: %d content hashes; local imports, Python syntax and workflow structure." % checked)
    print("This does not validate an implemented ComfyUI plugin or real GPU execution.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--strict", action="store_true", help="Verify all initial handoff bytes; use for the first transfer.")
    args = parser.parse_args()
    check(args.root, args.strict)
