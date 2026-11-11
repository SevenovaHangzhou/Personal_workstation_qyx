"""Build the product maintained on this branch for the current desktop OS."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MAINTAINED_PRODUCT = "work"


def main():
    if sys.platform not in ("linux", "win32"):
        raise SystemExit("Build on Linux x86_64 or Windows x64.")
    recipe = "build_windows.py" if sys.platform == "win32" else "build_release.py"
    env = dict(os.environ, ORBIT_APP_PROFILE=MAINTAINED_PRODUCT)
    return subprocess.call([sys.executable, str(ROOT/"scripts"/recipe), *sys.argv[1:]], cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
