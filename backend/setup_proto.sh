#!/usr/bin/env bash
# Generates Python gRPC stubs from Yellowstone proto files.
# Run from the backend/ directory:
#   bash setup_proto.sh
#
# If your venv is elsewhere, set VENV_PYTHON:
#   VENV_PYTHON=/path/to/.venv/bin/python bash setup_proto.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROTO_DIR="$SCRIPT_DIR/proto"
OUT_DIR="$SCRIPT_DIR/app/proto_gen"

# Detect Python with grpc_tools
if [ -n "$VENV_PYTHON" ]; then
  PY="$VENV_PYTHON"
elif [ -f "$SCRIPT_DIR/../.venv/bin/python" ]; then
  PY="$SCRIPT_DIR/../.venv/bin/python"
else
  PY="python"
fi

echo "Using Python: $PY"

mkdir -p "$OUT_DIR"

"$PY" -m grpc_tools.protoc \
  -I"$PROTO_DIR" \
  --python_out="$OUT_DIR" \
  --grpc_python_out="$OUT_DIR" \
  "$PROTO_DIR/solana-storage.proto" \
  "$PROTO_DIR/geyser.proto"

# Fix absolute imports -> relative imports inside the package
"$PY" - <<'PYEOF'
import os, re
out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "proto_gen")
for fname in sorted(os.listdir(out_dir)):
    if not fname.endswith(".py") or fname == "__init__.py":
        continue
    fpath = os.path.join(out_dir, fname)
    with open(fpath) as f:
        content = f.read()
    patched = re.sub(
        r'^import (geyser_pb2|solana_storage_pb2)',
        r'from . import \1',
        content,
        flags=re.MULTILINE,
    )
    patched = patched.replace(
        "from solana_storage_pb2 import *",
        "from .solana_storage_pb2 import *",
    )
    if patched != content:
        with open(fpath, "w") as f:
            f.write(patched)
        print(f"  patched: {fname}")
PYEOF

touch "$OUT_DIR/__init__.py"
echo "Proto stubs generated in $OUT_DIR"
