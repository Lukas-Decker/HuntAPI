"""Diff two recovered schema versions to see what a Hunt patch changed.

    python tools/diff_protos.py proto/2.8.0.54 proto/2.8.1.18

Reports added/removed services, RPCs, messages, enum values and message fields, plus
fields whose type or number moved (which is what silently breaks a decoder).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import schema

SCALARS = {
    1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32", 6: "fixed64",
    7: "fixed32", 8: "bool", 9: "string", 12: "bytes", 13: "uint32", 15: "sfixed32",
    16: "sfixed64", 17: "sint32", 18: "sint64",
}
LABELS = {1: "optional", 2: "required", 3: "repeated"}


def index(fds) -> dict:
    services, rpcs, messages, fields, enums = {}, {}, {}, {}, {}
    for fd in fds.file:
        if fd.name.startswith(("google/", "opentelemetry/")):
            continue
        for svc in fd.service:
            full = schema.qualified(fd, svc.name)
            services[full] = fd.name
            for m in svc.method:
                stream = ("stream " if m.client_streaming else "", "stream " if m.server_streaming else "")
                rpcs[f"{full}.{m.name}"] = f"({stream[0]}{m.input_type.lstrip('.')}) -> ({stream[1]}{m.output_type.lstrip('.')})"
        for full, msg in schema.walk_messages(fd):
            messages[full] = fd.name
            for f in msg.field:
                kind = f.type_name.lstrip(".") if f.type_name else SCALARS.get(f.type, f"type{f.type}")
                fields[f"{full}.{f.name}"] = (f.number, kind, LABELS.get(f.label, "?"))
        for enum in fd.enum_type:
            for value in enum.value:
                enums[f"{schema.qualified(fd, enum.name)}.{value.name}"] = value.number
    return {"services": services, "rpcs": rpcs, "messages": messages, "fields": fields, "enums": enums}


def report(title: str, old: dict, new: dict, show_value: bool = True) -> list[str]:
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in set(old) & set(new) if old[k] != new[k])
    if not (added or removed or changed):
        return []
    lines = [f"## {title}", ""]
    for k in added:
        lines.append(f"  + {k}" + (f"  {new[k]}" if show_value else ""))
    for k in removed:
        lines.append(f"  - {k}" + (f"  {old[k]}" if show_value else ""))
    for k in changed:
        lines.append(f"  ~ {k}: {old[k]} -> {new[k]}")
    lines.append("")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old", type=Path)
    ap.add_argument("new", type=Path)
    args = ap.parse_args()

    a = index(schema.load(args.old))
    b = index(schema.load(args.new))

    out = [f"# Schema diff: {args.old.name} -> {args.new.name}", ""]
    out += report("Services", a["services"], b["services"], show_value=False)
    out += report("RPCs", a["rpcs"], b["rpcs"])
    out += report("Messages", a["messages"], b["messages"], show_value=False)
    out += report("Fields", a["fields"], b["fields"])
    out += report("Enum values", a["enums"], b["enums"])

    if len(out) == 2:
        out.append("No schema changes.")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
