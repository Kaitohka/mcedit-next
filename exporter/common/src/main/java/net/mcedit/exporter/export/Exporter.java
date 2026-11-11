package net.mcedit.exporter.export;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import net.mcedit.exporter.pack.PackWriter;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.stream.Collectors;
import java.util.stream.Stream;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

/** Exports the blocks of a running game as a block pack. Must run on the client thread, after resources load. */
public final class Exporter {
    private final Game game;

    public Exporter(Game game) {
        this.game = game;
    }

    public static final class Result {
        public final Path folder;
        public final int blocks;
        public final int states;
        public final int problems;

        Result(Path folder, int blocks, int states, int problems) {
            this.folder = folder;
            this.blocks = blocks;
            this.states = states;
            this.problems = problems;
        }

        public String message() {
            String message = String.format("Exported %d blocks and %d states to %s", blocks, states, folder);
            if (problems > 0) {
                message += String.format(" (%d problems, see report.json)", problems);
            }
            return message;
        }
    }

    public Result export(Path folder, Scope scope) throws IOException {
        Report report = new Report();
        AssetExporter assets = new AssetExporter(game, folder, report);
        List<PackWriter.Block> blocks = new ArrayList<>();
        int states = 0;
        for (String id : game.blockIds()) {
            if (!scope.test(id)) {
                continue;
            }
            Game.BlockFacts facts = report.guarded(id, "the block", () -> game.readBlock(id, report), null);
            if (facts == null) {
                continue;
            }
            PackWriter.Block block = toBlock(facts, assets, report);
            blocks.add(block);
            states += block.states().size();
        }
        blocks.sort(Comparator.comparing(PackWriter.Block::id));

        List<PackWriter.BlockEntity> entities = report.guarded("block entities", "the list",
                () -> game.readBlockEntities(scope, report), Collections.<PackWriter.BlockEntity>emptyList());
        entities = new ArrayList<>(entities);
        entities.sort(Comparator.comparing(PackWriter.BlockEntity::id));
        JsonObject biomes = report.guarded("biomes", "the list", () -> game.readBiomes(scope, report), null);

        // Connected textures in OptiFine's format, as Continuity reads them, which no model names.
        List<String> connected = report.guarded("optifine/ctm", "the list", () -> game.listResources("optifine/ctm"),
                Collections.<String>emptyList());
        for (String name : connected) {
            // Forge 1.18 also lists a mod's whole assets folder, as "forge:".
            if (name.indexOf(':') >= 0 && name.substring(name.indexOf(':') + 1).startsWith("optifine/ctm/")) {
                assets.copyResource(name);
            }
        }

        PackWriter.write(folder, manifest(scope), blocks, entities, biomes, report.problems());
        return new Result(folder, blocks.size(), states, report.size());
    }

    private static PackWriter.Block toBlock(Game.BlockFacts facts, AssetExporter assets, Report report) {
        List<PackWriter.State> states = new ArrayList<>();
        for (Game.StateFacts state : facts.states) {
            Map<String, com.google.gson.JsonElement> attributes = state.attributes;
            attributes.put("resourcePath", new JsonPrimitive(state.modelLocation));
            attributes.put("resourceVariant", new JsonPrimitive(state.modelVariant));
            attributes.put("hasBlockstateFile", new JsonPrimitive(assets.hasBlockstateFile(state.modelLocation)));
            String source = report.guarded(facts.id, "modelSource",
                    () -> assets.exportBlockstate(state.modelLocation), "fallback");
            source = generatedModel(facts.id, state, source, attributes, assets, report);
            attributes.put("modelSource", new JsonPrimitive(source));
            attributes.put("particleTexture", new JsonPrimitive(state.particleTexture));
            if (!state.particleTexture.isEmpty()) {
                report.guarded(facts.id, "the particle texture's file", () -> {
                    assets.exportTexture(state.particleTexture);
                    return true;
                }, false);
            }
            states.add(new PackWriter.State(state.name, attributes));
        }
        return new PackWriter.Block(facts.id, facts.displayName, facts.descriptionId, facts.defaultState,
                facts.properties, states, facts.legacy);
    }

    // MCEdit's numbering of render types.
    private static final int RENDER_INVISIBLE = 0;
    private static final int RENDER_ENTITY = 2;
    private static final int RENDER_MODEL = 3;

    /**
     * For a block from a mod whose look its model files can't give, or which a block entity renderer draws, write
     * the faces the game draws into a model of their own, and name it in the state's "generatedModel". Returns the
     * state's modelSource: "generated" where that model is all there is to draw, or as it was where it is drawn
     * with the block's model files.
     */
    private static String generatedModel(String id, Game.StateFacts state, String source,
                                         Map<String, com.google.gson.JsonElement> attributes, AssetExporter assets,
                                         Report report) {
        if (id.startsWith("minecraft:") && !Boolean.getBoolean("mcedit.exporter.gameRenderers")) {
            return source;  // MCEdit draws the game's own blocks itself; the property is there to test the capture
        }
        int renderType = attributes.containsKey("renderType") ? attributes.get("renderType").getAsInt() : RENDER_MODEL;
        List<Quad> quads = new ArrayList<>();
        boolean replaces = !"json".equals(source) || state.customModel;
        if (replaces && renderType == RENDER_MODEL && state.modelQuads != null) {
            List<Quad> model = report.guarded(id, "the faces of its model", state.modelQuads::get, null);
            if (model != null) {
                quads.addAll(model);
            }
        }
        if (state.entityQuads != null && (renderType == RENDER_MODEL || renderType == RENDER_ENTITY
                || renderType == RENDER_INVISIBLE)) {
            List<Quad> entity = report.guarded(id, "the faces its block entity renderer draws", state.entityQuads::get,
                    null);
            if (entity != null) {
                quads.addAll(entity);
            }
        }
        // Nothing, the game's model for a block with none, or a renderer drawing with textures that aren't there, as
        // one can when it finds no world around the block: MCEdit shows those its own way, or from the model files.
        boolean anyTexture = false;
        for (Quad quad : quads) {
            anyTexture |= !quad.texture.endsWith(":missingno") && assets.textureExists(quad.texture);
        }
        if (!anyTexture) {
            return source;
        }
        String model = report.guarded(id, "its generated model", () -> assets.writeGeneratedModel(id, quads), null);
        if (model == null) {
            return source;
        }
        attributes.put("generatedModel", new JsonPrimitive(model));
        attributes.put("renderType", new JsonPrimitive(RENDER_MODEL));
        return replaces || renderType != RENDER_MODEL ? "generated" : source;
    }

    private JsonObject manifest(Scope scope) {
        JsonObject manifest = new JsonObject();
        manifest.addProperty("generatedAt", Instant.now().toString());
        manifest.addProperty("scope", scope.name().toLowerCase(Locale.ROOT));
        manifest.addProperty("minecraftVersion", game.minecraftVersion());
        if (game.dataVersion() > 0) {
            manifest.addProperty("dataVersion", game.dataVersion());
        }
        manifest.addProperty("loader", game.loader());
        if (game.loaderVersion() != null) {
            manifest.addProperty("loaderVersion", game.loaderVersion());
        }
        List<String[]> mods = new ArrayList<>(game.mods());
        mods.sort(Comparator.comparing((String[] mod) -> mod[0]));
        JsonArray modArray = new JsonArray();
        for (String[] mod : mods) {
            JsonObject entry = new JsonObject();
            entry.addProperty("id", mod[0]);
            entry.addProperty("version", mod[1]);
            modArray.add(entry);
        }
        manifest.add("mods", modArray);
        return manifest;
    }

    /**
     * Zip a pack into MCEdit's blockpacks folder. A pack holds thousands of small files, which MCEdit reads several
     * times faster from a zip. It is written under another name and then renamed, because MCEdit could otherwise
     * find a pack that is only partly written.
     */
    public static void zipPack(Path from, Path to) throws IOException {
        Files.createDirectories(to.getParent());
        Path partial = to.resolveSibling(to.getFileName() + ".partial");
        List<Path> files;
        try (Stream<Path> walk = Files.walk(from)) {
            files = walk.filter(Files::isRegularFile).collect(Collectors.toList());
        }
        try (ZipOutputStream zip = new ZipOutputStream(Files.newOutputStream(partial))) {
            for (Path file : files) {
                zip.putNextEntry(new ZipEntry(from.relativize(file).toString().replace('\\', '/')));
                try (InputStream in = Files.newInputStream(file)) {
                    copyStream(in, zip);
                }
                zip.closeEntry();
            }
        }
        Files.move(partial, to);
    }

    private static void copyStream(InputStream in, OutputStream out) throws IOException {
        byte[] buffer = new byte[8192];
        int read;
        while ((read = in.read(buffer)) > 0) {
            out.write(buffer, 0, read);
        }
    }
}
