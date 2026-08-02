"""Generated protobuf bindings for Hunt's client API.

protoc emits flat ``import Foo_pb2`` statements, so this directory has to be importable
as its own top-level path. Adding it to sys.path here lets the rest of huntapi do
``from huntapi.pb import MetaMissionBag_pb2`` without each generated module failing to
find its siblings.

Regenerate after re-running tools/dump_protos.py:

    cd proto/<version>
    python -m grpc_tools.protoc -I. --python_out=../../huntapi/pb *.proto
"""

import sys
from pathlib import Path

_here = str(Path(__file__).resolve().parent)
if _here not in sys.path:
    sys.path.insert(0, _here)
