# MCEdit exporter

A client-side Minecraft mod that writes a *block pack*: the blocks and block entities registered in the running client, in a format MCEdit reads. MCEdit never sees which game version or mod loader wrote a pack, so this is the boundary between them.

One jar works in all of these:

| Minecraft | Forge | NeoForge | Fabric |
|---|---|---|---|
| 1.7.10 | yes | | |
| 1.8, 1.8.8, 1.8.9, 1.9, 1.9.4, 1.10, 1.10.2, 1.11, 1.11.2, 1.12, 1.12.1, 1.12.2 | yes | | |
| 1.13.2 | yes | | |
| 1.14, 1.14.1, 1.14.2, 1.14.3 | | | yes |
| 1.14.4, 1.15, 1.15.1, 1.15.2 | yes | | yes |
| 1.16 | | | yes |
| 1.16.1 to 1.16.5 | yes | | yes |
| 1.17 | | | yes |
| 1.17.1, 1.18, 1.18.1, 1.18.2, 1.19 to 1.19.4, 1.20 | yes | | yes |
| 1.20.1 | yes | yes (as Forge) | yes |
| 1.20.2, 1.20.3, 1.20.4 | yes | yes | yes |
| 1.20.5 | | yes | yes |
| 1.20.6, 1.21, 1.21.1 | yes | yes | yes |
| 1.21.2 | | yes | yes |
| 1.21.3 to 1.21.11, 26.1, 26.1.1, 26.1.2, 26.2, 26.3 | yes | yes | yes |

That is every release of the game since 1.7.10 that a loader has a stable version for, except 1.7.2: Forge has none
for 1.13, 1.13.1, 1.14, 1.14.1, 1.16, 1.17, 1.20.5 and 1.21.2, and only betas for 1.14.2 and 1.14.3, which the jar
leaves out; Fabric begins with 1.14, and NeoForge with 1.20.2. On Fabric it needs Fabric API. In any other version it
loads and does nothing. NeoForge for 1.20.3, 1.20.5, 1.21.2, 1.21.6, 1.21.7, 1.21.9, 26.1 and 26.1.1 has only beta
versions, and the exporter was tested with those.

## How one jar does it

- `common/`: the pack format, the export, and the commands. No Minecraft types, compiled for Java 8, so every
  version shares it. `Game` is what it needs from the running game.
- `versions/<version>/`: reads blocks from that version of the game, written with Mojang's names. One copy serves
  every loader of that version. `1.16.5-forge` is the same code with the class names Forge used for 1.16.5.
- `targets/<version>-<loader>/`: a Gradle build for each version and loader, with the loader's glue in `src/glue`:
  commands, the tick event and the list of mods. It compiles the version's code and the glue into a package of its
  own, `net.mcedit.exporter.v<version>_<loader>`, and maps the game's names to the ones that loader uses at run time.
- `boot/`: the loaders' entry classes and metadata (`META-INF/mods.toml`, which NeoForge reads too until 1.20.4,
  `META-INF/neoforge.mods.toml`, `fabric.mod.json`, and `mcmod.info` for 1.7.10 to 1.12.2). Each loader reads only its own metadata and starts
  only its own entry class, which finds out the game's version and starts that target's glue. The other targets'
  classes are never loaded. Forge for 1.13 and later and Forge from 1.8.9 to 1.12.2 share one entry class: its
  annotation has the elements both of them read, and each ignores the ones it does not know. Forge for 1.7.10 has
  another, since its annotation is in another package.
- `build_universal.py`: builds every target and puts them in one jar. One build often serves several releases
  whose code is the same, as `ALSO_SERVES` says: the jar has a table of which build serves each release,
  `boot/targets.properties`, which the entry classes read.
- `test_universal.py`: starts each release with each loader, with the jar in its mods, and checks what it exports.

## Build

With JDK 21, and the Java runtimes that `scripts/versions/fetch_versions.py` downloads (see `gradle.properties`):

```bash
.venv/Scripts/python.exe exporter/build_universal.py
```

It writes `exporter/dist/mcedit_exporter-<version>-universal.jar`. The first run downloads Gradle, each version of
Minecraft, and each loader's tools, many gigabytes. Each loader has its own build tools, and some targets need a
Gradle of their own, so they have their own wrapper:

- Fabric: Loom. From 26.1 the game is no longer obfuscated, so Fabric's build does no remapping.
- NeoForge: ModDevGradle.
- Forge 1.7.10 and 1.12.2: RetroFuturaGradle, whose Gradle has to run on Java 25: their `gradle.properties` point
  at a JDK 25 in `worlds_local/java/25-jdk`.
- Forge 1.8.9 to 1.11.2: [Unimined](https://github.com/unimined/unimined), which maps MCP's names to the Searge
  names Forge runs mods with.
- Forge 1.18.2, 1.19.2, 1.19.3, 1.19.4 and 1.20.1: ModDevGradle's plugin for Forge.
- Forge 1.13.2 to 1.17.1, 1.20.4, and 1.20.6 to 1.21.10: ForgeGradle 6, with a wrapper for Gradle 8.8. Up to 1.20.4
  it maps to the SRG names Forge ran mods with then, and runs the game's tools on a JDK of the version that release
  needs: 8, 16 or 17, in `worlds_local/java`. ForgeGradle keeps the first project it sees, so these are built
  without a Gradle daemon. 1.13.2 has no names from Mojang, so its code uses MCP's.
- Forge 1.21.11 and 26.x: ForgeGradle 7, with a wrapper for Gradle 9.7. Its tools need a JDK 8, which they look for
  in their cache folder: `mklink /J %USERPROFILE%\.gradle\caches\minecraftforge\forgegradle\mavenizer\caches\jdk8 <a JDK 8>`
  saves them downloading one.

From 1.20.6, Forge runs mods with Mojang's names, so those targets do no remapping.

To check the jar in each version and loader, which opens a game window for each and takes a while:

```bash
.venv/Scripts/python.exe exporter/test_universal.py
```

It uses [portablemc](https://pypi.org/project/portablemc/) in `worlds_local/tools/launcher-venv` to install and start
the game offline.

## Use

A built jar is on the project's Releases page. Put the jar in the `mods` folder, enter any world, then run one of:

```text
/mcedit_export            blocks from mods only
/mcedit_export vanilla    Minecraft's own blocks only
/mcedit_export all        everything
```

Forge has client commands from 1.18.1. Before that, from 1.13.2 to 1.18, the command is typed in the chat box in the
same way, and is taken from it before it is sent. Fabric API has them from its builds for 1.16.5. With Fabric
from 1.14 to 1.16.4, the command is the game's own, so it is there only in a world the client runs itself: single
player, or one opened to the network.

The pack is written to `<game folder>/mcedit_export/<timestamp>/`. To put a zipped copy straight into MCEdit's
`blockpacks` folder, which is remembered after the first time:

```text
/mcedit_export install <MCEdit's blockpacks folder>
/mcedit_export install
```

A pack holds the blockstates, models and textures its blocks use, except those of the game itself. It is made from
files that belong to the game and to each mod's authors, so keep it for your own use and do not publish it.

Each block is drawn from its model files if MCEdit can read them. The composite loaders of NeoForge and Forge make a
model of ordinary models, and MCEdit draws them all. The OBJ loaders read a Wavefront `.obj` file: the pack gets it,
the `.mtl` files it names and their textures, and MCEdit draws its faces as NeoForge makes them, with the model's
`visibility`, `transform`, `automatic_culling`, `shade_quads`, `flip_v` and the materials' colors and tints.

Where the model files can't give a block's look, the exporter writes down the faces the game itself made, in a model
of its own in `models/mcedit_generated/`, with the loader `mcedit:quads`, and names it in the state's
`generatedModel`. That is so for a block whose model comes from a custom model loader, a B3D model, or a baked model a
mod makes in code (`modelSource` is then `generated`), and for a block entity renderer, whose faces are drawn with
the block's own model. A model whose block a renderer draws in the world may give its faces only for the item, as
HBM's machines do: then the item's are taken. A mod's own model loader that names a model file, as HBM's do, has the
file copied, an `.obj` file with its materials or a Collada `.dae` file, and MCEdit draws it about the middle of the
block where the game gives no faces, as for HBM's sliding blast door and transition seal. Faces drawn with a texture of their own, as entity models are, take that texture file along.
Some renderers draw nothing without a world: `test_universal.py --in-world` exports in a copy of the release's test
world, from 1.20, which can join a world when it starts. Block entity renderers before 1.15 draw with OpenGL
themselves, through display lists and matrices of their own: the exporter runs them in OpenGL's feedback mode, which
gives back every face drawn instead of drawing it, with a texture manager standing in for the game's to say which
texture each face was drawn with. From 1.8.9 to 1.12.2 the renderer is given a world of its one block, which some ask
what block they are: it is the game's world too while they draw, and its chunks answer with that block for those that
read a `ChunkCache`. A renderer is tried again without it if that is not enough. Renderers that need more, such as the
player, are listed in `report.json`. A block with none of this is a cube with its
particle texture.

Connected textures are drawn as the mods that make them do: the CTM mod's (`"ctm"` in a texture's `.mcmeta`, of
the kinds `ctm`, `ctmh`, `ctm_vertical`, `sctm`, `edges`, `edges_full`, `ctmv` or `pillar`, `eldritch`, and `random`
and `pattern`, which choose a cell of the texture by where the block is, and Chisel's own `ar`), Fusion's (`"fusion"`:
connecting textures in the layouts `full`, `simple`, `pieced`, `compact`, `horizontal`, `vertical` and `overlay`, with
a connecting model's `connections`, and the types `random`, `continuous` and `scrolling`), and OptiFine's properties
files in `optifine/ctm/`, as Continuity reads them (`ctm`, `ctm_compact`, `horizontal`, `vertical`,
`horizontal+vertical`, `vertical+horizontal`, `top`, `fixed`, `random` and `repeat`, and the overlays `overlay`,
`overlay_ctm`, `overlay_random`, `overlay_repeat` and `overlay_fixed`, drawn just in front of the face and tinted with
the biome's grass or foliage where `tintBlock` says). Textures chosen by where the block is come out as in the game,
with the same random numbers. The pack gets the textures they are made of. `cullsSameBlock` says which blocks hide their faces against the same block, as glass
does. `report.json` lists anything that could not be read.

## Packs from 1.12.2 and earlier

Worlds from these versions keep blocks by number and data value, and Forge gives each mod's blocks their numbers
when a world is made, so the same block has different numbers in different worlds. A pack from these versions also
says, for each block, what each data value is. MCEdit takes the numbers from the world's `level.dat`, where Forge
writes them, and the rest from the pack. It uses the packs exported from the same version as the world. Worlds from
1.7.10 and 1.8.9 have no data version, and are told apart by what Forge wrote in `level.dat`.

1.8.9 to 1.12.2 have blockstate files, and MCEdit draws their blocks from them as in later versions, including
Forge's own format, the files with `"forge_marker": 1`, which many mods use: a block's look is put together from
defaults, a part for each property's value and parts for whole states, which can give models other textures and
add submodels. OBJ models are drawn as in later versions, with `"flip-v"` in `"custom"` turning their textures
upside down; B3D models and models made in code are drawn from the faces the game made of them. 1.7.10 has no
blockstate or model files, since code draws every block: there each data value becomes a state, `meta=N`, with a
model made from the texture the game gives each side. A block drawn as crossed squares, such as a flower, gets those,
a block with a renderer of its own from a mod gets the faces that renderer draws, alone in a world of air, and any
other block gets a cube. Chisel's textures named `name|x.y` are one cell of the texture `name`, and the model shows
that cell of it.

## Development tools (NeoForge 1.21.1)

`mc1211-neoforge/` is the first exporter, for NeoForge 1.21.1 alone. It is kept for the tools below, which make the
worlds MCEdit's tests read. Run them from that folder.

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

Then copy `run/saves/mcedit_modded_121` to `worlds_local/`. The pack comes from the universal jar, since this
folder's own exporter is older. Run `./gradlew testmodClasses`, put `build/classes/java/testmod` and
`build/resources/testmod` together in a jar, and from the repository root export with it in a NeoForge 1.21.1
client:

```
.venv/Scripts/python.exe exporter/test_universal.py --only 1.21.1-neoforge --scope mods --extra-mod <the test mod jar>
```

Copy the newest folder in `worlds_local/exporter_test/1.21.1-neoforge/mcedit_export` to `worlds_local/testmod_pack`.
`tests/test_mcedit2/exporter_pipeline_test.py` reads the world and the pack, and is skipped without them.

To try real mods, put them in `run/mods` and leave out `-Pmcedit.testMod`. The generated world then has every block
from those mods in a grid at Y=210. Take them out again before regenerating anything else. Or give them to
`test_universal.py` with `--extra-mod`, once each, with `--in-world` from 1.20.

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

This table only holds facts about blocks and their English names. Textures and models are read from the user's own Minecraft jar at run time. Packs exported from a modded client hold the blockstates, models and textures of the mods' blocks, so they must not be redistributed.
