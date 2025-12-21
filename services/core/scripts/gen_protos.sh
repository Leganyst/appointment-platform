#!/usr/bin/env bash
set -euo pipefail

# Generate Go stubs for all protos under internal/api with stable, source-relative paths.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROTO_DIR="$ROOT_DIR/internal/api"

GOBIN="$(go env GOBIN)"
if [[ -z "$GOBIN" ]]; then
  GOBIN="$(go env GOPATH)/bin"
fi
export PATH="$GOBIN:$PATH"

PROTOC_BIN="${PROTOC:-$(command -v protoc || true)}"
if [[ -z "$PROTOC_BIN" ]]; then
  echo "protoc not found; install protoc or set PROTOC=/path/to/protoc" >&2
  exit 1
fi
if [[ "$PROTOC_BIN" == /snap/bin/* ]]; then
  echo "warning: protoc from snap may not work in some environments; consider installing protobuf-compiler or setting PROTOC=/path/to/protoc" >&2
fi

"$PROTOC_BIN" \
  -I "$PROTO_DIR" \
  -I "$ROOT_DIR" \
  --go_out=paths=source_relative:"$PROTO_DIR" \
  --go-grpc_out=paths=source_relative:"$PROTO_DIR" \
  $(find "$PROTO_DIR" -name '*.proto' -print | sed "s|^$PROTO_DIR/||")
