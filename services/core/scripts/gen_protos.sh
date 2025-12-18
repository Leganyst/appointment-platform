#!/usr/bin/env bash
set -euo pipefail

# Generate Go stubs for all protos under internal/api with stable, source-relative paths.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROTO_DIR="$ROOT_DIR/internal/api"

protoc \
  -I "$PROTO_DIR" \
  -I "$ROOT_DIR" \
  --go_out=paths=source_relative:"$ROOT_DIR" \
  --go-grpc_out=paths=source_relative:"$ROOT_DIR" \
  $(find "$PROTO_DIR" -name '*.proto' -print)
