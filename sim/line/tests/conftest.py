import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "line", ROOT / "magnet_sorter", ROOT / "coffee_sorter"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
