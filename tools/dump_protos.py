"""Recover Hunt's client API schema from the game binary.

The protobuf C++ runtime keeps every generated file's ``FileDescriptorProto`` in the
binary as one serialised blob, so the complete API definition can be read straight out
of ``GameHunt.dll`` without running the game.

    python tools/dump_protos.py --game "E:/SteamLibrary/steamapps/common/Hunt Showdown 1896"

Writes ``proto/<version>/*.proto`` plus a ``manifest.json`` describing the build the
schema came from.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from google.protobuf import descriptor_pb2

# Descriptors for these are shipped with protobuf itself; no point committing copies.
VENDORED = ("google/", "opentelemetry/")

SCALARS = {
    1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32", 6: "fixed64",
    7: "fixed32", 8: "bool", 9: "string", 12: "bytes", 13: "uint32", 15: "sfixed32",
    16: "sfixed64", 17: "sint32", 18: "sint64",
}
LABELS = {1: "", 2: "required ", 3: "repeated "}

# FileDescriptorProto tops out at field 13 (edition); anything higher means we have
# walked off the end of the message and into whatever the linker put next.
MAX_FILE_FIELD = 13


def read_varint(buf: bytes, i: int) -> tuple[int | None, int]:
    result = shift = 0
    while i < len(buf):
        byte = buf[i]
        result |= (byte & 0x7F) << shift
        i += 1
        if not byte & 0x80:
            return result, i
        shift += 7
        if shift > 63:
            break
    return None, i


def find_descriptor_starts(data: bytes) -> list[tuple[int, str]]:
    """Locate every blob that opens with field 1 (name) holding a ``*.proto`` string."""
    starts = {}
    for match in re.finditer(rb"\.proto", data):
        end = match.end()
        lo = match.start()
        while lo > 0 and 0x20 <= data[lo - 1] < 0x7F and chr(data[lo - 1]) not in " \"'":
            lo -= 1
        # The length prefix is itself often a printable byte, so the run we just walked
        # back over may include it. Try every split point rather than guessing.
        for begin in range(lo, match.start() + 1):
            name = data[begin:end]
            if len(name) < 6:
                continue
            length_at = begin - 1
            length, after = read_varint(data, length_at)
            if after == begin and length == len(name) and length_at > 0 and data[length_at - 1] == 0x0A:
                starts[length_at - 1] = name.decode()
                break
    return sorted(starts.items())


def scan_message_end(data: bytes, start: int) -> int:
    """Walk top-level fields from ``start`` until one does not look like a descriptor."""
    i = start
    while i < len(data):
        tag, j = read_varint(data, i)
        if tag is None:
            break
        field, wire = tag >> 3, tag & 7
        if field == 0 or field > MAX_FILE_FIELD or wire not in (0, 1, 2, 5):
            break
        if wire == 0:
            value, j = read_varint(data, j)
            if value is None:
                break
        elif wire == 2:
            length, j = read_varint(data, j)
            if length is None or j + length > len(data):
                break
            j += length
        elif wire == 1:
            j += 8
        else:
            j += 4
        i = j
    return i


def parse_descriptors(data: bytes) -> tuple[list[descriptor_pb2.FileDescriptorProto], list[str]]:
    files, failed = [], []
    for start, name in find_descriptor_starts(data):
        end = scan_message_end(data, start)
        for stop in range(end, start, -1):
            fd = descriptor_pb2.FileDescriptorProto()
            try:
                if fd.MergeFromString(data[start:stop]) == stop - start and fd.name == name:
                    files.append(fd)
                    break
            except Exception:
                continue
        else:
            failed.append(name)
    return files, failed


def type_name(field) -> str:
    return field.type_name.lstrip(".") if field.type_name else SCALARS.get(field.type, f"type{field.type}")


def render_field(field, maps: dict[str, tuple[str, str]]) -> str:
    """Render one field. ``maps`` resolves synthetic map-entry types back to map<k,v>."""
    entry = maps.get(field.type_name.lstrip("."))
    if entry and field.label == 3:
        return f"map<{entry[0]}, {entry[1]}> {field.name} = {field.number};"
    return f"{LABELS.get(field.label, '')}{type_name(field)} {field.name} = {field.number};"


def collect_map_entries(msg, prefix: str, out: dict[str, tuple[str, str]]) -> None:
    """protoc turns every map<k,v> into a hidden nested XxxEntry message. Undo that."""
    for nested in msg.nested_type:
        full = f"{prefix}.{nested.name}"
        if nested.options.map_entry:
            by_number = {f.number: f for f in nested.field}
            out[full] = (type_name(by_number[1]), type_name(by_number[2]))
        else:
            collect_map_entries(nested, full, out)


def render_enum(enum, indent: str) -> list[str]:
    lines = [f"{indent}enum {enum.name} {{"]
    if any(v.number == 0 for v in enum.value) and len({v.number for v in enum.value}) < len(enum.value):
        lines.append(f"{indent}  option allow_alias = true;")
    lines += [f"{indent}  {v.name} = {v.number};" for v in enum.value]
    return lines + [f"{indent}}}"]


def render_message(msg, indent: str, maps: dict[str, tuple[str, str]]) -> list[str]:
    lines = [f"{indent}message {msg.name} {{"]
    for enum in msg.enum_type:
        lines += render_enum(enum, indent + "  ")
    for nested in msg.nested_type:
        if nested.options.map_entry:
            continue  # protoc regenerates these from the map<> field itself
        lines += render_message(nested, indent + "  ", maps)
    for field in msg.field:
        lines.append(f"{indent}  {render_field(field, maps)}")
    return lines + [f"{indent}}}"]


def render_file(fd: descriptor_pb2.FileDescriptorProto, maps: dict[str, tuple[str, str]]) -> str:
    lines = [f'syntax = "{fd.syntax or "proto2"}";', f"package {fd.package};", ""]
    lines += [f'import "{dep}";' for dep in fd.dependency]
    if fd.dependency:
        lines.append("")
    for enum in fd.enum_type:
        lines += render_enum(enum, "")
    for msg in fd.message_type:
        lines += render_message(msg, "", maps)
    for service in fd.service:
        lines.append(f"service {service.name} {{")
        for method in service.method:
            req = ("stream " if method.client_streaming else "") + method.input_type.lstrip(".")
            rsp = ("stream " if method.server_streaming else "") + method.output_type.lstrip(".")
            lines.append(f"  rpc {method.name}({req}) returns ({rsp});")
        lines.append("}")
    return "\n".join(lines) + "\n"


@dataclass
class BuildInfo:
    version: str
    binary: str
    size: int


def binary_version(path: Path) -> str:
    """Read the PE VS_FIXEDFILEINFO version. Windows only, which is where the game is."""
    import ctypes
    import ctypes.wintypes as wt

    ver = ctypes.WinDLL("version")
    size = ver.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return "unknown"
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
        return "unknown"
    block = ctypes.c_void_p()
    length = wt.UINT()
    if not ver.VerQueryValueW(buf, "\\", ctypes.byref(block), ctypes.byref(length)):
        return "unknown"
    fixed = ctypes.cast(block, ctypes.POINTER(ctypes.c_uint32 * 4)).contents
    ms_file, ls_file = fixed[2], fixed[3]
    return f"{ms_file >> 16}.{ms_file & 0xFFFF}.{ls_file >> 16}.{ls_file & 0xFFFF}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", required=True, help="Hunt: Showdown 1896 install root")
    ap.add_argument("--out", default="proto", help="output directory (default: proto)")
    ap.add_argument("--flat", action="store_true", help="write into --out directly, not out/<version>")
    args = ap.parse_args()

    dll = Path(args.game) / "bin" / "win_x64" / "GameHunt.dll"
    if not dll.is_file():
        print(f"GameHunt.dll not found under {args.game}", file=sys.stderr)
        return 1

    data = dll.read_bytes()
    version = binary_version(dll)
    print(f"{dll.name}  {len(data):,} bytes  version {version}", file=sys.stderr)

    files, failed = parse_descriptors(data)
    for name in failed:
        print(f"  could not parse {name}", file=sys.stderr)
    print(f"recovered {len(files)} descriptors ({len(failed)} failed)", file=sys.stderr)

    out = Path(args.out) if args.flat else Path(args.out) / version
    out.mkdir(parents=True, exist_ok=True)

    maps: dict[str, tuple[str, str]] = {}
    for fd in files:
        for msg in fd.message_type:
            collect_map_entries(msg, f"{fd.package}.{msg.name}" if fd.package else msg.name, maps)

    written, services, rpcs = [], 0, 0
    for fd in sorted(files, key=lambda f: f.name):
        if fd.name.startswith(VENDORED):
            continue
        target = out / fd.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_file(fd, maps), encoding="utf-8")
        written.append(fd.name)
        services += len(fd.service)
        rpcs += sum(len(s.method) for s in fd.service)

    manifest = {
        "game_version": version,
        "binary": dll.name,
        "binary_size": len(data),
        "descriptors_found": len(files) + len(failed),
        "descriptors_parsed": len(files),
        "files_written": len(written),
        "services": services,
        "rpcs": rpcs,
        "files": sorted(written),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(written)} .proto files, {services} services, {rpcs} rpcs -> {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
