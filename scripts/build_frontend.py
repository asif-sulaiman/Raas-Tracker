"""Build the frontend and publish it to the directory Flask serves.

`react_frontend/` is gitignored and holds the built SPA that `flask_app.py`
serves. The build output is content-hashed, so a copy that only adds files
leaves the previous bundle's hashed assets behind and the served shell can
reference a bundle that no longer exists. This script therefore replaces the
built parts of the target and leaves everything else in it alone.

    python scripts/build_frontend.py              # build, then publish
    python scripts/build_frontend.py --skip-build # publish an existing dist/

Vercel does the same copy in its own build command, so production never needs
this; it exists so a local Flask serve is never stale.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "raas-tracker-frontend"
DIST = FRONTEND / "dist"
TARGET = ROOT / "react_frontend"

# Everything the build writes. The target's other contents (the Windows
# installer, for one) are left untouched.
BUILT_ENTRIES = ("assets", "icons", "index.html", "manifest.json", "icons.svg", "raas-logo.png")


def copy_dist(dist_dir: Path = DIST, target_dir: Path = TARGET) -> list[str]:
    """Replace the built parts of `target_dir` with the contents of `dist_dir`.

    Returns the names that were published.
    """
    if not dist_dir.is_dir():
        raise SystemExit(f"no build output at {dist_dir} - run the build first")
    target_dir.mkdir(parents=True, exist_ok=True)

    published: list[str] = []
    for name in BUILT_ENTRIES:
        source = dist_dir / name
        if not source.exists():
            continue
        destination = target_dir / name
        if destination.is_dir():
            shutil.rmtree(destination)
        elif destination.exists():
            destination.unlink()
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
        published.append(name)
    return published


def build(frontend_dir: Path = FRONTEND) -> None:
    """Run the frontend build."""
    print(f"building the frontend in {frontend_dir}", file=sys.stderr)
    subprocess.run(["npm.cmd", "run", "build"] if sys.platform == "win32" else ["npm", "run", "build"],
                   cwd=frontend_dir, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true",
                        help="publish the existing dist/ without rebuilding")
    args = parser.parse_args()

    if not args.skip_build:
        build()
    published = copy_dist()
    print(f"published to {TARGET}: {', '.join(published)}")


if __name__ == "__main__":
    main()
