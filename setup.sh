#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "Creating venv with Python 3.13..."
/opt/homebrew/bin/python3.13 -m venv .venv

echo "Installing dependencies..."
.venv/bin/pip install mcp qrcode

echo "Done. MCP server is ready."
