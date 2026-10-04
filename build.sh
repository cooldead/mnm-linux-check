#!/bin/bash
# Builds dist/mnm-linux-check.py: the GUI with mnm-linux-check.sh embedded, as one file for players.
set -euo pipefail
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }

mkdir -p dist
python3 - <<'EOF'
from pathlib import Path
script = Path("mnm-linux-check.sh").read_text()
if "'''" in script or script.endswith("\\"):
    raise SystemExit("mnm-linux-check.sh can't be embedded in a ''' string as-is")
gui = Path("gui/mnm_check_gui.py").read_text()
marker = "r'''@@CHECK_SCRIPT@@'''"
assert gui.count(marker) == 1
Path("dist/mnm-linux-check.py").write_text(gui.replace(marker, "r'''" + script + "'''"))
EOF
chmod +x dist/mnm-linux-check.py
python3 -m py_compile dist/mnm-linux-check.py
echo "Built dist/mnm-linux-check.py"
