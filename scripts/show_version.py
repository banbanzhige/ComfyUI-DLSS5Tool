"""Show installed plugin and pinned dependency identities without GPU/network."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dlss5tool_comfy.release_info import version_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print a shareable report without private paths")
    args = parser.parse_args()
    try:
        report = version_report()
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"Version check failed: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"ComfyUI-DLSS5Tool v{report['plugin_version']} | catalog r{report['catalog_revision']}")
        host = report["host"]
        print(f"Host ABI {host['abi']} r{host['revision']}: {'verified' if host['verified'] else 'INVALID'} ({host['sha256'][:16]})")
        for runtime in report["runtimes"]:
            print(f"NR {runtime['profile']} r{runtime['revision']}: {runtime['cache_status']} ({runtime['sha256'][:16]})")
        print(f"Python {report['python']}")
        for package in report["python_requirements"]:
            print(f"{package['package']}: required {package['required']}, installed {package['installed'] or 'missing'}")
    return 0 if report["host"]["verified"] and all(r["cache_status"] != "invalid" for r in report["runtimes"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
