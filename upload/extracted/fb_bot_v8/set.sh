#!/bin/bash
# ============================================
#  FB Auto-Comment Bot v8 - Setup Script
#  GitHub Codespace / Ubuntu / Debian
# ============================================

set -e

echo ""
echo "  FB Bot v8 Setup"
echo ""

# Install pip if needed
if ! command -v pip3 &> /dev/null; then
    echo "  Installing pip..."
    sudo apt-get update -qq
    sudo apt-get install -y python3-pip -qq
fi

# Install dependencies
echo "  Installing flask..."
pip3 install flask --quiet

echo "  Installing playwright..."
pip3 install playwright --quiet

echo "  Installing browser..."
playwright install chromium
playwright install-deps chromium 2>/dev/null || true

echo ""
echo "  Done!"
echo ""
echo "  Run bot:"
echo "    cd $(dirname "$0")"
echo "    python3 main.py"
echo ""
echo "  Buka port 8080 di Codespace untuk login."
echo ""
