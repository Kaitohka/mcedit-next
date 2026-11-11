# Development setup

Upgrade plan: [PLAN_PY3_1.21.md](PLAN_PY3_1.21.md). The original Python 2 source is at [mcedit/mcedit2](https://github.com/mcedit/mcedit2).

The code runs on Python 3.12 and PySide6. It opens, edits and saves worlds from every release of Minecraft from 1.7.10 to 26.3. The plan for the other versions is [PLAN_CROSS_VERSION.md](PLAN_CROSS_VERSION.md).

## Requirements

- Python 3.12
- Windows: Microsoft C++ Build Tools with the "Desktop development with C++" workload, for the Cython modules
- A GPU driver with an OpenGL compatibility profile. macOS is not a supported target.

## Create the environment

```bash
python scripts/setup_env.py
```

This creates `.venv` from the pinned `requirements-py3.txt`, builds the Cython modules, then runs `scripts/check_env.py`, which really compiles a Cython extension and runs the legacy OpenGL calls the renderer depends on.

After changing a `.pyx` file, rebuild with the script for that package:

```bash
.venv/Scripts/python.exe setup_mceditlib.py build_ext --inplace
```

```bash
.venv/Scripts/python.exe setup_mcedit2.py build_ext --inplace
```

## Run the application

```bash
PYTHONPATH=src .venv/Scripts/python.exe -m mcedit2.main
```

If no Minecraft 1.12 or newer install is found, it asks before downloading the 1.12 client from Mojang.

## Build the program

With the Cython modules built and the `.ui` files compiled (running the application from source once does that), and PyInstaller installed in `.venv`:

```bash
.venv/Scripts/python.exe -m PyInstaller mcedit2.spec --noconfirm
```

This makes `dist/MCEdit2/`, with `mcedit2.exe` in it. Copy the whole folder to use it. Its user files, such as settings, block packs and Minecraft jars, are kept in `MCEdit 2 Files/` inside that folder. Rebuilding deletes `dist/MCEdit2/`, including those files, so don't keep anything there that you need.

The build bundles the Minecraft 1.21.1 client jar if it is in `MCEdit 2 Files/versionCache/`. The jar belongs to Mojang and may not be distributed, so build a package to publish with `MCEDIT_PUBLIC_BUILD=1` set, which leaves it out.

The exporter mod is built separately: see `exporter/README.md`.

## Translations

The interface is in English and Simplified Chinese. MCEdit follows the system language unless one is chosen in Options → Language / 语言 or in Preferences → General, and a change applies the next time it starts.

Interface text has to go through `self.tr("...")`, `QCoreApplication.translate("Context", "...")`, or `QT_TRANSLATE_NOOP("Context", "...")` for text kept in a class attribute, so that it can be collected. After changing interface text, run:

```bash
.venv/Scripts/python.exe scripts/update_translations.py
```

This adds new text to `src/mcedit2/i18n/zh_CN.ts` and compiles `zh_CN.qm`. Translate the new entries in the `.ts` with Qt Linguist (`.venv/Scripts/pyside6-linguist.exe`) or a text editor, then run it again. Untranslated text is shown in English. The build compiles the `.qm` files itself.

## Themes

MCEdit has three looks, chosen in Preferences → General:
- **Modern dark:** the default.
- **Modern light.**
- **Classic:** the system's own look that MCEdit had before.

A change applies at once. The modern themes keep the layout and icons and redraw everything else. Their colours are in `src/mcedit2/util/theme.py`, and the stylesheet they fill in is `src/mcedit2/styles/modern.qss`. The arrows, check mark and dock buttons it uses are drawn by:

```bash
.venv/Scripts/python.exe scripts/make_theme_icons.py
```

Load MCEdit's own monochrome icons with `theme.icon(path)` rather than `QtGui.QIcon(path)`. On the dark theme, it draws their dark outlines light. Spin boxes are left out of the stylesheet on purpose: styled there, Qt makes them much wider.

## Run the tests

```bash
.venv/Scripts/python.exe -m pytest tests/test_mceditlib
```

To also drive the real application, which opens windows on your screen:

```bash
.venv/Scripts/python.exe scripts/smoke_ui.py --out worlds_local/smoke
.venv/Scripts/python.exe scripts/smoke_ui.py --world worlds_local/mcedit_test_121 --out worlds_local/smoke
```

Add `--stub-assets` if you have no Minecraft jar. It only proves that code runs without errors: check the saved screenshots for what it drew.

`tests/test_mceditlib/python3_port_test.py` covers behaviour that changes silently between Python 2 and 3, or NumPy 1 and 2, and round-trips every chunk of the baseline world.

To change dependencies, edit `requirements-py3.in`, install it into `.venv`, then regenerate the pins:

```bash
.venv/Scripts/python.exe -m pip freeze --disable-pip-version-check > requirements-py3.txt
```

On Windows, keep the virtualenv on a short path: PySide6 installs files about 165 characters below it and fails past the 260 character limit unless long paths are enabled.

## Versions of Minecraft

Each release from 1.13 on has a format profile in `src/mceditlib/anvil/format_profile.py`, and a block pack in
`src/mceditlib/blocktypes/packs/` listing its blocks and states. Releases with the same blocks share a pack;
`packs/releases.json` says which. The packs, and `blocktypes/flattening.json` for chunks still saved as 1.12.2 saved
them, are made from the game itself by the scripts in `scripts/versions/`. To make them again, or to check every
release after a change:

```bash
.venv/Scripts/python.exe scripts/versions/fetch_versions.py
.venv/Scripts/python.exe scripts/versions/make_reports.py
.venv/Scripts/python.exe scripts/versions/make_block_packs.py
.venv/Scripts/python.exe scripts/versions/make_test_worlds.py --accept-eula
.venv/Scripts/python.exe scripts/versions/make_flattening_table.py --accept-eula
.venv/Scripts/python.exe scripts/versions/check_worlds.py
.venv/Scripts/python.exe scripts/versions/check_models.py
.venv/Scripts/python.exe scripts/versions/check_saving.py --accept-eula
```

`check_saving.py` saves changes to a copy of each release's test world, has the release's own server load and save
the chunks again, and compares every block. Run it after changing how chunks are saved.

`fetch_versions.py` downloads the server and client of each release and the Java versions they need, about 4.5 GB,
into `worlds_local/`. The jars belong to Mojang: keep them on your own computer. `make_test_worlds.py`,
`make_flattening_table.py` and `check_saving.py` start Minecraft servers, which means agreeing to the Minecraft EULA; they do nothing without
`--accept-eula`. `make_reports.py` runs the data generator, which starts no server.

To add a release, add it to `RELEASES` in `format_profile.py` with the data version its worlds are saved with, run the
scripts for it with `--only`, and check the results.

## Test worlds

**Keep a backup of any world you edit.** Opening a world no longer changes its files, but saving is not atomic: a crash or power cut while saving can leave a region file damaged. Test on copies.

- `test_files/`: committed fixtures. Their hashes are recorded in `scripts/test_files.manifest.json`.
- `MCEdit 2 Files/versionCache/`: client jars the application has downloaded, named `1.12.jar` and `1.21.1.jar`.
  Tests that load real block models are skipped if `1.21.1.jar` is missing.
- `MCEdit 2 Files/blockpacks/`: block packs, as folders or zip files. A pack is used for a world from its game
  version if it covers a mod in the world's `fml.LoadingModList`, or for any world that has no such list.
- `MCEdit 2 Files/defined_blocks.json`: block definitions, written by Configure Blocks or by hand. See
  `BlockDefinition` in `src/mcedit2/dialogs/configure_blocks.py`.
- `worlds_local/`: put copies of real worlds here, such as a 1.21.1 world for M4. It is git-ignored. To make the
  world that the tests read, see `exporter/README.md`.

`AnvilWorld_1.8` is the regression baseline for the port.

Check that fixtures are intact:

```bash
.venv/Scripts/python.exe scripts/world_hash.py verify test_files scripts/test_files.manifest.json
```

Prove that opening a world left it untouched, using a manifest stored outside the world:

```bash
.venv/Scripts/python.exe scripts/world_hash.py snapshot worlds_local/MyWorld worlds_local/MyWorld.manifest.json
```

```bash
.venv/Scripts/python.exe scripts/world_hash.py verify worlds_local/MyWorld worlds_local/MyWorld.manifest.json
```
