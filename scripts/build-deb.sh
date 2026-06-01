#!/usr/bin/env bash
# Build a .deb package for TinyAgentOS
# Usage: ./scripts/build-deb.sh [version]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

VERSION="${1:-$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")}"

echo "[build-deb] building tinyagentos_${VERSION}_amd64.deb"

echo "[build-deb] (1/4) building SPA frontend..."
cd desktop
npm ci --silent
npm run build
cd "$PROJECT_DIR"

echo "[build-deb] (2/4) staging files..."
mkdir -p /tmp/deb/opt/tinyagentos
cp -r tinyagentos /tmp/deb/opt/tinyagentos/
cp -r data /tmp/deb/opt/tinyagentos/
cp -r app-catalog /tmp/deb/opt/tinyagentos/
cp -r static /tmp/deb/opt/tinyagentos/
cp pyproject.toml /tmp/deb/opt/tinyagentos/
mkdir -p /tmp/deb/lib/systemd/system
cp systemd/tinyagentos.service /tmp/deb/lib/systemd/system/

echo "[build-deb] (3/4) creating post-install script..."
cat > /tmp/deb-postinst.sh << 'SCRIPT'
#!/bin/bash
set -e
cd /opt/tinyagentos
python3 -m venv .venv
. .venv/bin/activate
pip install -e . --quiet
systemctl daemon-reload
systemctl enable tinyagentos.service
systemctl start tinyagentos.service || true
SCRIPT
chmod +x /tmp/deb-postinst.sh

echo "[build-deb] (4/4) building .deb with fpm..."
sudo gem install fpm --no-document 2>/dev/null || true
fpm -s dir -t deb \
    -n tinyagentos \
    -v "$VERSION" \
    --description "Self-hosted AI agent platform" \
    --license "AGPL-3.0-or-later" \
    --url "https://github.com/jaylfc/tinyagentos" \
    --vendor "jaylfc" \
    --maintainer "jaylfc" \
    --depends "python3 >= 3.11" \
    --depends "python3-pip" \
    --depends "python3-venv" \
    --depends "libtorrent-rasterbar-dev" \
    --depends "libsqlcipher-dev" \
    --depends "git" \
    --depends "curl" \
    --after-install /tmp/deb-postinst.sh \
    -C /tmp/deb \
    opt/ lib/

mv tinyagentos_*.deb "tinyagentos_${VERSION}_amd64.deb"
echo "[build-deb] done: tinyagentos_${VERSION}_amd64.deb"
