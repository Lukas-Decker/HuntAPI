"""Shared helper: load a recovered proto directory as a FileDescriptorSet."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from google.protobuf import descriptor_pb2


def latest_version_dir(root: Path) -> Path:
    """Newest ``proto/<version>/`` by version tuple, or ``root`` itself if it holds protos."""
    if any(root.glob("*.proto")):
        return root
    versions = [d for d in root.iterdir() if d.is_dir() and any(d.glob("*.proto"))]
    if not versions:
        raise SystemExit(f"no .proto files under {root}")

    def key(d: Path) -> tuple:
        return tuple(int(p) if p.isdigit() else -1 for p in d.name.split("."))

    return max(versions, key=key)


def load(proto_dir: Path) -> descriptor_pb2.FileDescriptorSet:
    """Compile every .proto in the directory and return the parsed descriptor set."""
    files = sorted(p.name for p in proto_dir.glob("*.proto"))
    if not files:
        raise SystemExit(f"no .proto files in {proto_dir}")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "set.bin"
        cmd = [sys.executable, "-m", "grpc_tools.protoc", "-I.", f"-o{out}", *files]
        result = subprocess.run(cmd, cwd=proto_dir, capture_output=True, text=True)
        errors = [l for l in result.stderr.splitlines() if "warning" not in l]
        if result.returncode != 0:
            raise SystemExit("protoc failed:\n" + "\n".join(errors))
        fds = descriptor_pb2.FileDescriptorSet()
        fds.ParseFromString(out.read_bytes())
        return fds


def qualified(fd: descriptor_pb2.FileDescriptorProto, name: str) -> str:
    return f"{fd.package}.{name}" if fd.package else name


def walk_messages(fd: descriptor_pb2.FileDescriptorProto):
    """Yield (full_name, DescriptorProto) for every message including nested ones."""

    def rec(msg, prefix):
        full = f"{prefix}.{msg.name}"
        if not msg.options.map_entry:
            yield full, msg
        for nested in msg.nested_type:
            yield from rec(nested, full)

    for msg in fd.message_type:
        yield from rec(msg, fd.package)
