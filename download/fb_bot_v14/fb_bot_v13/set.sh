#!/bin/bash
# ============================================
#  FB Auto-Comment Bot v13 - Setup Script
#  GitHub Codespace / Ubuntu / Debian
#  Optimized for 2GB RAM server
# ============================================

set -e

echo ""
echo "  FB Bot v13 Setup - Lightweight Live + SSE"
echo ""

# Install pip if needed
if ! command -v pip3 &> /dev/null; then
    echo "  Installing pip..."
    sudo apt-get update -qq
    sudo apt-get install -y python3-pip -qq
fi

echo "  Installing flask..."
pip3 install flask --quiet

echo "  Installing playwright..."
pip3 install playwright --quiet

echo "  Installing chromium (minimal)..."
playwright install chromium
playwright install-deps chromium 2>/dev/null || true

echo ""
echo "  Done!"
echo ""
echo "  Run bot:"
echo "    cd $(dirname "$0")"
echo "    export FB='your_cookie_string'"
echo "    python3 main.py"
echo ""
echo "  Buka port 8080 di Codespace untuk live view."
echo "  SSE status indicator di pojok kiri bawah browser."
echo ""
