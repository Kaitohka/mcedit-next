# MCEdit exporter

A client-side Minecraft mod that writes a *block pack*: the blocks and block entities registered in the running client, in a format MCEdit reads. MCEdit never sees which game version or mod loader wrote a pack, so this is the boundary between them.

- `common/`: the pack format. No Minecraft types, so every game version can share it.
- `mc1211-neoforge/`: reads the game on Minecraft 1.21.1 with NeoForge. Other versions or loaders would be sibling folders.

## Build

From `mc1211-neoforge/`, with JDK 21:

```bash
./gradlew build
```

The first run downloads Gradle, Minecraft and NeoForge.

## Use

Put the jar from `build/libs/` in the `mods` folder, enter any world, then run one of:

```text
/mcedit_export            blocks from mods only
/mcedit_export vanilla    Minecraft's own blocks only
/mcedit_export all        everything
```

The pack is written to `<game folder>/mcedit_export/<timestamp>/`. To put a zipped copy straight into MCEdit's
`blockpacks` folder, which is remembered after the first time:

```text
/mcedit_export install <MCEdit's blockpacks folder>
/mcedit_export install
```

A pack holds the blockstates, models and textures its blocks use, except those of the game itself. It is made from
files that belong to the game and to each mod's authors, so keep it for your own use and do not publish it.

Each block is drawn from its model files if MCEdit can read them. Otherwise it is a cube with the block's particle
texture. That goes for blocks with no model files, and for those using a custom model loader or connected textures.
A block with moving parts that are drawn by code shows only what its model files describe. `report.json` lists
anything that could not be read.

## Make the test world

MCEdit's tests read a small world made by the game. This creates it in `run/saves/`, with blocks and entities
that `tests/test_mceditlib/modern_world_test.py` looks for, and exits:

```
./gradlew runClient -Pmcedit.genTestWorld=mcedit_test_121
```

Then copy `run/saves/mcedit_test_121` to `worlds_local/` at the top of the repository. Those tests are skipped
if it is not there.

A name with `debug` in it, such as `mcedit_debug_121`, makes a world of the game's debug type. It has every state of
every block in a grid at Y=70, which is useful for checking how they all look.

## Test the whole way through

`src/testmod` is a small mod with one block of each kind that the exporter has to deal with. This makes a world with
those blocks, and runs the export command in it as a player would:

```
./gradlew runClient -Pmcedit.testMod -Pmcedit.genTestWorld=mcedit_modded_121 "-Pmcedit.genTestWorldCommand=mcedit_export"
```

Then copy `run/saves/mcedit_modded_121` to `worlds_local/`, and the newest folder in `run/mcedit_export` to
`worlds_local/testmod_pack`. `tests/test_mcedit2/exporter_pipeline_test.py` reads them, and is skipped without them.

To try real mods, put them in `run/mods` and leave out `-Pmcedit.testMod`. The generated world then has every block
from those mods in a grid at Y=210. Take them out again before regenerating anything else.

## Check that the game accepts what MCEdit saves

`scripts/game_load_check.py` edits a copy of the test world, then checks what the game makes of it. This loads a
world with the game, reports what it finds at the places listed in `run/mcedit_check_request.json`, saves it and exits:

```
./gradlew runClient -Pmcedit.checkWorld=<name of a world in run/saves>
```

## Regenerate the built-in Minecraft block table

This starts the client, exports once it has loaded, then exits:

```bash
./gradlew runClient -Pmcedit.autoExport=vanilla
```

Then, from the repository root:

```bash
.venv/Scripts/python.exe scripts/validate_block_pack.py exporter/mc1211-neoforge/run/mcedit_export/<timestamp> --assets <path to the 1.21.1 client jar>
```

Gradle keeps that jar at `~/.gradle/caches/neoformruntime/artifacts/minecraft_1.21.1_client.jar`. If it passes, copy the pack's files over `src/mceditlib/blocktypes/packs/minecraft_1_21_1/`.

This table only holds facts about blocks and their English names. Textures and models are read from the user's own Minecraft jar at run time. Packs exported from a modded client contain nothing from the mods' assets yet, and must not be redistributed once they do.
