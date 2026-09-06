#!/bin/bash
# Build standalone worker apps for distribution
# Requires: pip install pyinstaller
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"

case "$OS" in
    linux)
        echo "Building Linux worker..."
        pyinstaller --onefile --name tinyagentos-worker-linux tinyagentos/worker/__main__.py
        ;;
    darwin)
        echo "Building macOS worker..."
        pyinstaller --onefile --noconsole --name tinyagentos-worker-macos \
            --osx-bundle-identifier com.tinyagentos.worker \
            tinyagentos/worker/__main__.py
        ;;
    mingw*|msys*|cygwin*)
        echo "Building Windows worker..."
        pyinstaller --onefile --noconsole --name tinyagentos-worker-windows \
            tinyagentos/worker/__main__.py
        ;;
    *)
        echo "Unknown platform: $OS"
        exit 1
        ;;
esac

echo "Done. Output in dist/"
