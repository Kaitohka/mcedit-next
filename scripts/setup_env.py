#!/usr/bin/env python3
"""Create the development virtualenv, build the Cython modules, then check the environment.

Usage: python scripts/setup_env.py [--venv PATH] [--skip-check]
Uses only the standard library, so it runs before any dependency is installed.
"""
import argparse
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_PYTHON = (3, 12)


def venv_python(venv_dir):
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv", type=Path, default=ROOT / ".venv", help="virtualenv directory (default: .venv)")
    parser.add_argument("--skip-check", action="store_true", help="do not run scripts/check_env.py afterwards")
    args = parser.parse_args()

    if sys.version_info[:2] != REQUIRED_PYTHON:
        found = ".".join(map(str, sys.version_info[:3]))
        wanted = ".".join(map(str, REQUIRED_PYTHON))
        sys.exit(f"Python {wanted} is required, but this is Python {found}.")

    # PySide6 installs files about 165 characters below the virtualenv folder, which fails
    # past Windows' 260 character limit unless long paths are enabled.
    if os.name == "nt" and len(str(args.venv.resolve())) + 165 > 259:
        print(f"Warning: {args.venv} is a deep path. If installing PySide6 fails, use a shorter --venv path.")

    python = venv_python(args.venv)
    if python.exists():
        print(f"Reusing virtualenv at {args.venv}")
    else:
        print(f"Creating virtualenv at {args.venv}")
        venv.create(args.venv, with_pip=True)

    subprocess.run(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(ROOT / "requirements-py3.txt")],
        check=True,
    )

    for script in ("setup_mceditlib.py", "setup_mcedit2.py"):
        subprocess.run([str(python), script, "build_ext", "--inplace"], cwd=ROOT, check=True)

    if args.skip_check:
        return 0
    return subprocess.run([str(python), str(ROOT / "scripts" / "check_env.py")]).returncode


if __name__ == "__main__":
    sys.exit(main())
