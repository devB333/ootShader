#!/bin/sh
# One-time setup after cloning, on Linux or WSL: gets the Lichii sources the tools build against (build-125, the
# build the bundled hints were recorded with) into ./lichii, where the tools look by default. Afterwards:
#   python3 tools/scb_oot.py --nup "/path/to/game.nup" [--emulator fritchii]
# Needs git, g++ (C++20) and Python 3.14 (or 3.13 with backports.zstd).
set -e
cd "$(dirname "$0")"
for tool in git g++ python3; do
    command -v "$tool" >/dev/null 2>&1 || { echo "missing: $tool (Ubuntu: sudo apt install git g++ python3)"; exit 1; }
done
python3 -c "
try:
    from compression import zstd
except ImportError:
    from backports import zstd
" 2>/dev/null || { echo "Python 3.14 is needed (or 3.13 with: pip install backports.zstd)"; exit 1; }
if [ -e lichii/tools/nx2/extract_static_shaders.py ]; then
    echo "lichii/ is already there"
else
    git clone --depth 1 --branch build-125 https://github.com/zortos293/Lichii lichii
fi
echo
echo "Ready. Close the emulator, then run:"
echo "  python3 tools/scb_oot.py --nup \"/path/to/game.nup\"                       (Lichii, Eden)"
echo "  python3 tools/scb_oot.py --nup \"/path/to/game.nup\" --emulator fritchii   (Fritchii v5.8.x)"
