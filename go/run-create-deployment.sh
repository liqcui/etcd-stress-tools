#!/bin/bash
# Wrapper script to run create-deployment.go without binary execution issues
# This avoids macOS code signing problems with go build

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Use go run instead of compiled binary to avoid dyld code signing issues
exec go run create-deployment.go "$@"
