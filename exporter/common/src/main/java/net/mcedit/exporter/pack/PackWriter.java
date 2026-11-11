package net.mcedit.exporter.pack;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;

import java.io.IOException;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.zip.GZIPOutputStream;

/**
 * Writes a block pack. This is the format MCEdit reads, and must not depend on Minecraft.
 *
 * <pre>
 * manifest.json        what wrote the pack, and for which game
 * blocks.json.gz       every block and block state
 * block_entities.json  every block entity type, and the blocks that use it
 * biomes.json          optional: colors and climate of biomes, if the game was in a world
 * report.json          what could not be read from the game
 * assets/              the game files that the blocks need, in the layout of a resource pack
 * </pre>
 *
 * Each block keeps an attribute under "common" when all of its states share the value, and under
 * the state otherwise, which makes the file many times smaller. Increase {@link #SCHEMA_VERSION}
 * when a reader of the old format would misread the new one.
 *
 * This runs in every version of the game the exporter supports, so it is written for Java 8 and for
 * the oldest Gson those versions have.
 */
public final class PackWriter {
    public static final int SCHEMA_VERSION = 1;

    public static final class State {
        private final String name;
        private final Map<String, JsonElement> attributes;

        public State(String name, Map<String, JsonElement> attributes) {
            this.name = name;
            this.attributes = attributes;
        }

        public String name() {
            return name;
        }

        public Map<String, JsonElement> attributes() {
            return attributes;
        }
    }

    public static final class Block {
        private final String id;
        private final String displayName;
        private final String descriptionId;
        private final String defaultState;
        private final Map<String, List<String>> properties;
        private final List<State> states;
        private final JsonObject legacy;

        public Block(String id, String displayName, String descriptionId, String defaultState,
                     Map<String, List<String>> properties, List<State> states) {
            this(id, displayName, descriptionId, defaultState, properties, states, null);
        }

        /**
         * @param legacy for versions that save blocks by number, before 1.13: "numericId", the block's number in the
         *               game that exported it, and "metaStates", the state each data value reads as. Null otherwise.
         */
        public Block(String id, String displayName, String descriptionId, String defaultState,
                     Map<String, List<String>> properties, List<State> states, JsonObject legacy) {
            this.id = id;
            this.displayName = displayName;
            this.descriptionId = descriptionId;
            this.defaultState = defaultState;
            this.properties = properties;
            this.states = states;
            this.legacy = legacy;
        }

        public String id() {
            return id;
        }

        public String displayName() {
            return displayName;
        }

        public String descriptionId() {
            return descriptionId;
        }

        public String defaultState() {
            return defaultState;
        }

        public Map<String, List<String>> properties() {
            return properties;
        }

        public List<State> states() {
            return states;
        }

        public JsonObject legacy() {
            return legacy;
        }
    }

    public static final class BlockEntity {
        private final String id;
        private final List<String> blocks;
        private final Boolean hasRenderer;
        private final String defaultNbt;

        /**
         * @param hasRenderer whether the game draws it with code of its own, or null if that could not be found out
         * @param defaultNbt  what a new one saves, as SNBT to keep the types of its tags, or null if that failed
         */
        public BlockEntity(String id, List<String> blocks, Boolean hasRenderer, String defaultNbt) {
            this.id = id;
            this.blocks = blocks;
            this.hasRenderer = hasRenderer;
            this.defaultNbt = defaultNbt;
        }

        public String id() {
            return id;
        }

        public List<String> blocks() {
            return blocks;
        }

        public Boolean hasRenderer() {
            return hasRenderer;
        }

        public String defaultNbt() {
            return defaultNbt;
        }
    }

    private PackWriter() {
    }

    /**
     * @param manifest   game details. The schema version and the counts are added here.
     * @param biomes     biomes by name, or null if none are known
     * @param problems   anything that could not be read, reported rather than failing the export
     */
    public static void write(Path folder, JsonObject manifest, List<Block> blocks, List<BlockEntity> blockEntities,
                             JsonObject biomes, List<String> problems) throws IOException {
        Files.createDirectories(folder);
        Gson gson = new GsonBuilder().disableHtmlEscaping().create();
        Gson pretty = new GsonBuilder().disableHtmlEscaping().setPrettyPrinting().create();

        JsonArray blockArray = new JsonArray();
        int stateCount = 0;
        for (Block block : blocks) {
            blockArray.add(blockJson(block));
            stateCount += block.states().size();
        }
        JsonObject blocksFile = new JsonObject();
        blocksFile.add("blocks", blockArray);
        try (Writer writer = new OutputStreamWriter(
                new GZIPOutputStream(Files.newOutputStream(folder.resolve("blocks.json.gz"))), StandardCharsets.UTF_8)) {
            gson.toJson(blocksFile, writer);
        }

        JsonArray entityArray = new JsonArray();
        for (BlockEntity entity : blockEntities) {
            JsonObject entry = new JsonObject();
            entry.addProperty("id", entity.id());
            entry.add("blocks", strings(entity.blocks()));
            if (entity.hasRenderer() != null) {
                entry.addProperty("hasRenderer", entity.hasRenderer());
            }
            if (entity.defaultNbt() != null) {
                entry.addProperty("defaultNbt", entity.defaultNbt());
            }
            entityArray.add(entry);
        }
        JsonObject entitiesFile = new JsonObject();
        entitiesFile.add("blockEntities", entityArray);
        writeText(folder.resolve("block_entities.json"), pretty.toJson(entitiesFile));

        if (biomes != null) {
            JsonObject biomesFile = new JsonObject();
            biomesFile.add("biomes", biomes);
            writeText(folder.resolve("biomes.json"), pretty.toJson(biomesFile));
        }

        JsonObject report = new JsonObject();
        report.add("problems", strings(problems));
        writeText(folder.resolve("report.json"), pretty.toJson(report));

        // Last, so that a manifest means the rest of the pack was written completely.
        JsonObject counts = new JsonObject();
        counts.addProperty("blocks", blocks.size());
        counts.addProperty("states", stateCount);
        counts.addProperty("blockEntities", blockEntities.size());
        counts.addProperty("problems", problems.size());

        JsonObject fullManifest = new JsonObject();
        fullManifest.addProperty("schemaVersion", SCHEMA_VERSION);
        for (Map.Entry<String, JsonElement> entry : manifest.entrySet()) {
            fullManifest.add(entry.getKey(), entry.getValue());
        }
        fullManifest.add("counts", counts);
        writeText(folder.resolve("manifest.json"), pretty.toJson(fullManifest));
    }

    /** Write a file from the game to the pack, at a path like "assets/somemod/textures/block/thing.png". */
    public static void writeAsset(Path folder, String path, byte[] contents) throws IOException {
        Path file = folder.resolve(path);
        Files.createDirectories(file.getParent());
        Files.write(file, contents);
    }

    private static void writeText(Path file, String text) throws IOException {
        Files.write(file, text.getBytes(StandardCharsets.UTF_8));
    }

    private static JsonArray strings(List<String> values) {
        JsonArray array = new JsonArray();
        for (String value : values) {
            array.add(new JsonPrimitive(value));
        }
        return array;
    }

    private static JsonObject blockJson(Block block) {
        JsonObject json = new JsonObject();
        json.addProperty("id", block.id());
        json.addProperty("displayName", block.displayName());
        json.addProperty("descriptionId", block.descriptionId());
        json.addProperty("defaultState", block.defaultState());

        JsonObject properties = new JsonObject();
        for (Map.Entry<String, List<String>> entry : block.properties().entrySet()) {
            properties.add(entry.getKey(), strings(entry.getValue()));
        }
        json.add("properties", properties);

        JsonObject common = new JsonObject();
        List<String> sharedNames = new ArrayList<>();
        if (!block.states().isEmpty()) {
            for (Map.Entry<String, JsonElement> entry : block.states().get(0).attributes().entrySet()) {
                boolean shared = true;
                for (State state : block.states()) {
                    if (!entry.getValue().equals(state.attributes().get(entry.getKey()))) {
                        shared = false;
                        break;
                    }
                }
                if (shared) {
                    common.add(entry.getKey(), entry.getValue());
                    sharedNames.add(entry.getKey());
                }
            }
        }
        json.add("common", common);

        JsonArray states = new JsonArray();
        for (State state : block.states()) {
            JsonObject stateJson = new JsonObject();
            stateJson.addProperty("state", state.name());
            for (Map.Entry<String, JsonElement> entry : state.attributes().entrySet()) {
                if (!sharedNames.contains(entry.getKey())) {
                    stateJson.add(entry.getKey(), entry.getValue());
                }
            }
            states.add(stateJson);
        }
        json.add("states", states);
        if (block.legacy() != null) {
            json.add("legacy", block.legacy());
        }
        return json;
    }
}
