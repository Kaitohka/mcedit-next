package net.mcedit.exporter.export;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.mcedit.exporter.pack.PackWriter;

import java.io.IOException;
import java.io.InputStream;
import java.util.List;
import java.util.Map;
import java.util.function.Predicate;
import java.util.function.Supplier;

/**
 * What the exporter needs from the running game. Each version of the game, and each mod loader, has its own
 * implementation; everything else in the exporter is the same for all of them.
 *
 * Names of blocks, block entities, biomes and resources are given as "namespace:path" strings.
 */
public interface Game {
    String minecraftVersion();

    /** The data version worlds are saved with, or 0 if this version of the game has none. */
    int dataVersion();

    /** "forge", "neoforge" or "fabric" */
    String loader();

    /** The mod loader's version, or null if it can't be found out. */
    String loaderVersion();

    /** The loaded mods as {id, version} pairs. */
    List<String[]> mods();

    /** Every block's name, in the order of the game's registry. */
    List<String> blockIds();

    /** Read a block and all of its states. Anything that can't be read goes in the report. */
    BlockFacts readBlock(String id, Report report);

    List<PackWriter.BlockEntity> readBlockEntities(Predicate<String> include, Report report);

    /** Biomes by name, as the game's own biome files describe them, or null if the game is not in a world. */
    JsonObject readBiomes(Predicate<String> include, Report report);

    /** Open a resource such as "somemod:textures/block/thing.png", or return null if there is none. */
    InputStream openResource(String name) throws IOException;

    /**
     * The resources in a folder and those in it, in every namespace, as "namespace:folder/path", or an empty list where
     * this version can't list them.
     */
    default List<String> listResources(String folder) {
        return java.util.Collections.emptyList();
    }

    /** Whether the resource comes from the game itself, which MCEdit has, rather than a mod or resource pack. */
    boolean isGameResource(String name);

    /**
     * Whether blockstate files name models from models/block/, as "stone" for models/block/stone.json, as they did
     * before 1.13. From 1.13 they give the whole path, "minecraft:block/stone".
     */
    default boolean blockstateModelsInBlockFolder() {
        return false;
    }

    /** Packages of the game, its mod loaders, and mods that only change textures, whose models the files describe. */
    String[] MODEL_PACKAGES = {"net.minecraft.", "net.neoforged.", "net.minecraftforge.", "net.fabricmc.", "com.mojang.",
            "team.chisel.ctm.", "com.supermartijn642.fusion.", "me.pepperbell.continuity.", "cpw.mods."};

    /** Whether a baked model is a mod's own class, rather than one the game or its loader made from model files. */
    static boolean isModelFromMod(Object model) {
        if (model == null) {
            return false;
        }
        String name = model.getClass().getName();
        for (String prefix : MODEL_PACKAGES) {
            if (name.startsWith(prefix)) {
                return false;
            }
        }
        return true;
    }

    /** What the exporter learns about one block. */
    final class BlockFacts {
        public final String id;
        public final String displayName;
        public final String descriptionId;
        public final String defaultState;
        public final Map<String, List<String>> properties;
        public final List<StateFacts> states;
        /** See PackWriter.Block: the block's number and the state of each data value, before 1.13. Else null. */
        public JsonObject legacy;

        public BlockFacts(String id, String displayName, String descriptionId, String defaultState,
                          Map<String, List<String>> properties, List<StateFacts> states) {
            this.id = id;
            this.displayName = displayName;
            this.descriptionId = descriptionId;
            this.defaultState = defaultState;
            this.properties = properties;
            this.states = states;
        }
    }

    /**
     * One block state. The attributes are those the game gives directly: opacity, light and so on. The exporter
     * adds what it works out from the files, using the model's location and the particle texture.
     */
    final class StateFacts {
        public final String name;
        public final Map<String, JsonElement> attributes;
        /** The blockstate file the state's model comes from, as "namespace:path" without "blockstates/". */
        public final String modelLocation;
        /** The variant of the blockstate file, such as "facing=north". */
        public final String modelVariant;
        /** The texture the game shows when the block breaks, as "namespace:path", or "" if it has none. */
        public final String particleTexture;
        /**
         * The faces of the state's model as the game has made it, or null where this version can't give them. Read
         * only for a block whose model files can't give its look, as one the game makes in code.
         */
        public Supplier<List<Quad>> modelQuads;
        /**
         * The faces the block's block entity renderer draws for this state, or null if it has none or this version
         * can't give them. Read only for blocks from mods.
         */
        public Supplier<List<Quad>> entityQuads;
        /**
         * Whether the state's model is a mod's own, made in code from its model files or in place of them, so that the
         * files can't be trusted to give its look: its faces are drawn as the game made them instead.
         */
        public boolean customModel;

        public StateFacts(String name, Map<String, JsonElement> attributes, String modelLocation, String modelVariant,
                          String particleTexture) {
            this.name = name;
            this.attributes = attributes;
            this.modelLocation = modelLocation;
            this.modelVariant = modelVariant;
            this.particleTexture = particleTexture;
        }
    }
}
