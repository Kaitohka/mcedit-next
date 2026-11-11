#!/usr/bin/env python3
"""Build MCEdit's block models for each release from its own client, and report the blocks it can't draw.

Usage: python scripts/versions/check_models.py [--only ID ...]

Run scripts/versions/fetch_versions.py and make_block_packs.py first. For every format profile this loads the
profile's block pack and the client of the release it takes assets from, cooks every block state's model as
opening a world does, and lists the states that would be drawn as unknown blocks, with the warnings logged
on the way. Releases up to 1.12.2 all use the 1.12.2 client and the 1.12 tables, and are checked once.
"""
import argparse
import collections
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_versions import ROOT, VERSIONS  # noqa: E402

sys.path.insert(0, str(ROOT / "src"))
from mceditlib.anvil import format_profile  # noqa: E402

MODEL_RENDER_TYPE = 3


class Collect(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = collections.Counter()

    def emit(self, record):
        message = record.getMessage().splitlines()[0]
        self.messages[message[:160]] += 1


def check(name, world, jar):
    from mcedit2.rendering.blockmodels import BlockModels
    from mcedit2.rendering.textureatlas import TextureAtlas
    from mcedit2.resourceloader import ResourceLoader
    from mceditlib.worldeditor import WorldEditor

    if not (world / "level.dat").exists():
        print("%-16s no test world at %s" % (name, world), flush=True)
        return None
    handler = Collect()
    logging.getLogger().addHandler(handler)
    start = time.time()
    editor = WorldEditor(str(world), readonly=True)
    try:
        blocktypes = editor.blocktypes
        loader = ResourceLoader(str(jar))
        for pack in getattr(editor.adapter, "packSelection", None).packs if hasattr(editor.adapter, "packSelection") else ():
            loader.addBlockPack(pack)
        loader.addZipFile(str(jar))
        models = BlockModels(blocktypes, loader)
        TextureAtlas(editor, loader, models, overrideMaxSize=16384)  # cooks the models
    finally:
        logging.getLogger().removeHandler(handler)
        editor.close()

    undrawn = []
    total = 0
    for block in blocktypes:
        if block.renderType != MODEL_RENDER_TYPE:
            continue
        total += 1
        if not models.hasModel(block.ID, block.meta):
            undrawn.append(block.nameAndState)
    print("%-16s %-8s states %5d undrawn %4d warnings %4d  %.1fs" % (
        name, jar.parent.name, total, len(undrawn), sum(handler.messages.values()), time.time() - start), flush=True)
    names = collections.Counter(state.partition("[")[0] for state in undrawn)
    if names:
        print("    undrawn blocks:", ", ".join("%s (%d)" % pair for pair in names.most_common(12)))
    for message, count in handler.messages.most_common(5):
        print("    warning x%d: %s" % (count, message))
    return len(undrawn)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*", help="profiles whose names include any of these")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)

    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841, textures need it

    if not args.only or any(o in ("1.12.2", "old") for o in args.only):
        check("1.7.10-1.12.2", VERSIONS / "1.12.2" / "run" / "normal", VERSIONS / "1.12.2" / "client.jar")
    for profile in format_profile.PROFILES:
        if args.only and not any(o in profile.name for o in args.only):
            continue
        folder = VERSIONS / profile.assetsVersion
        check(profile.name, folder / "run" / "normal", folder / "client.jar")


if __name__ == "__main__":
    sys.exit(main())
