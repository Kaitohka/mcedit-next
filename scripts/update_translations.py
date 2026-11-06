#!/usr/bin/env python3
"""Update the interface translations.

    python scripts/update_translations.py [--release-only]

1. lupdate collects every translatable string from src/mcedit2 (the .ui files, self.tr(),
   QCoreApplication.translate() and QT_TRANSLATE_NOOP()) into src/mcedit2/i18n/<lang>.ts.
   Translations already in the .ts are kept; new strings are added as unfinished.
2. lrelease compiles each .ts to the .qm file that MCEdit loads.

Translate new strings by editing the .ts, with Qt Linguist (pyside6-linguist) or a text editor,
then run this script again. Strings without a translation are shown in English.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "mcedit2"
I18N = SRC / "i18n"
LANGUAGES = ["zh_CN"]


def tool(name):
    """Find pyside6-<name> next to this Python, or in the PySide6 package."""
    exe = ".exe" if os.name == "nt" else ""
    candidates = [Path(sys.executable).parent / ("pyside6-" + name + exe),
                  Path(sys.executable).parent / "Scripts" / ("pyside6-" + name + exe)]
    try:
        import PySide6
        candidates.append(Path(PySide6.__file__).parent / (name + exe))
    except ImportError:
        pass
    for c in candidates:
        if c.exists():
            return str(c)
    sys.exit("Could not find %s. Is PySide6 installed in this Python?" % name)


def sourceFiles():
    files = sorted(SRC.rglob("*.ui"))
    for p in sorted(SRC.rglob("*.py")):
        rel = p.relative_to(SRC).parts
        # The .py files compiled from .ui files repeat the .ui strings.
        if rel[0] == "ui" or "test" in p.name or "tests" in rel:
            continue
        files.append(p)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--release-only", action="store_true", help="only compile the .ts files")
    args = parser.parse_args()

    if not args.release_only:
        listFile = I18N / "sources.lst"
        listFile.write_text("\n".join(str(p) for p in sourceFiles()) + "\n", encoding="utf-8")
        try:
            for lang in LANGUAGES:
                ts = I18N / (lang + ".ts")
                subprocess.run([tool("lupdate"), "@" + str(listFile), "-locations", "none",
                                "-source-language", "en", "-target-language", lang, "-ts", str(ts)],
                               check=True)
        finally:
            listFile.unlink()

    for lang in LANGUAGES:
        ts = I18N / (lang + ".ts")
        subprocess.run([tool("lrelease"), str(ts), "-qm", str(I18N / (lang + ".qm"))], check=True)


if __name__ == "__main__":
    main()
