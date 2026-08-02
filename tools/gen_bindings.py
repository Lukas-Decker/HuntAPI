"""Compile the recovered .proto files into Python bindings under huntapi/pb.

    python tools/gen_bindings.py

Run this once after cloning, and again after tools/dump_protos.py picks up a new game
build. The generated *_pb2.py files are not committed (they are derivable and large).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "huntapi" / "pb"


def main() -> int:
    proto_dir = schema.latest_version_dir(ROOT / "proto")
    files = sorted(p.name for p in proto_dir.glob("*.proto"))
    if not files:
        print(f"no .proto files in {proto_dir}", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "grpc_tools.protoc", "-I.", f"--python_out={OUT}", *files]
    result = subprocess.run(cmd, cwd=proto_dir, capture_output=True, text=True)
    errors = [l for l in result.stderr.splitlines() if "warning" not in l]
    if result.returncode != 0:
        print("protoc failed:\n" + "\n".join(errors), file=sys.stderr)
        return result.returncode
    count = len(list(OUT.glob("*_pb2.py")))
    print(f"generated {count} bindings from {proto_dir.name} -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
