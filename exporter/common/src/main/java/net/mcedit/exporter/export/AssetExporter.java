package net.mcedit.exporter.export;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.mcedit.exporter.pack.PackWriter;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Copies the files a block needs from the game's resources into the pack: its blockstate file, the models that
 * file names and their parents, and their textures. The game's own files are left out, since MCEdit reads those
 * from the game. Also works out whether MCEdit can draw the block from these files.
 */
final class AssetExporter {
    /** Loaders that make a model from other models, each written out in full in the model's file. */
    private static final Set<String> COMPOSITE_LOADERS = new HashSet<>(Arrays.asList(
            "neoforge:composite", "forge:composite"));
    /**
     * Loaders whose models are written like the game's, with elements, and only add to them: Fusion's, whose
     * connecting textures MCEdit draws from the textures' .mcmeta files.
     */
    private static final Set<String> PLAIN_LOADERS = new HashSet<>(Arrays.asList("fusion:model"));
    /** Loaders that read a Wavefront .obj file, which MCEdit draws. */
    private static final Set<String> OBJ_LOADERS = new HashSet<>(Arrays.asList("neoforge:obj", "forge:obj"));

    private final Game game;
    private final Path folder;
    private final Report report;
    private final Set<String> copied = new HashSet<>();
    private final Map<String, boolean[]> models = new HashMap<>();  // {has elements, uses a loader}
    private final Map<String, String> blockstateSources = new HashMap<>();
    private final Map<String, Boolean> blockstateFiles = new HashMap<>();

    AssetExporter(Game game, Path folder, Report report) {
        this.game = game;
        this.folder = folder;
        this.report = report;
    }

    boolean hasBlockstateFile(String location) {
        Boolean known = blockstateFiles.get(location);
        if (known == null) {
            known = exists(file(location, "blockstates/", ".json"));
            blockstateFiles.put(location, known);
        }
        return known;
    }

    /**
     * Copy a block's blockstate file and what it needs, and return "json" if MCEdit can draw the block from them,
     * or "fallback" if the game makes its look some other way.
     */
    String exportBlockstate(String location) {
        String known = blockstateSources.get(location);
        if (known == null) {
            known = readBlockstate(location);
            blockstateSources.put(location, known);
        }
        return known;
    }

    private String readBlockstate(String location) {
        String file = file(location, "blockstates/", ".json");
        JsonObject json = readJson(file);
        if (json == null) {
            return "fallback";
        }
        copy(file);

        List<String> modelNames = new ArrayList<>();
        if (json.has("forge_marker")) {
            collectForgeParts(json, modelNames);
        } else if (json.has("variants")) {
            for (Map.Entry<String, JsonElement> entry : json.getAsJsonObject("variants").entrySet()) {
                collectModels(entry.getValue(), modelNames);
            }
        }
        if (json.has("multipart")) {
            for (JsonElement part : json.getAsJsonArray("multipart")) {
                JsonElement apply = part.getAsJsonObject().get("apply");
                if (apply != null) {
                    collectModels(apply, modelNames);
                }
            }
        }

        boolean anyElements = false;
        boolean anyLoader = false;
        for (String name : modelNames) {
            if (name.endsWith(".obj")) {
                // Forge 1.12's OBJ models, named from models/, or from models/block/ in a vanilla blockstate file
                boolean found = exportObj(name, game.blockstateModelsInBlockFolder() && !json.has("forge_marker"));
                anyElements |= found;
                anyLoader |= !found;
                continue;
            }
            if (name.substring(name.lastIndexOf('/') + 1).contains(".")) {
                anyLoader = true;  // Forge's B3D models, which code makes into a model
                continue;
            }
            if (game.blockstateModelsInBlockFolder()) {
                int colon = name.indexOf(':');
                name = name.substring(0, colon + 1) + "block/" + name.substring(colon + 1);
            }
            boolean[] model = readModel(name);
            anyElements |= model[0];
            anyLoader |= model[1];
        }
        return anyElements && !anyLoader ? "json" : "fallback";
    }

    /** What a part of a Forge blockstate file may say. A key of "variants" with any of these is a whole state. */
    private static final Set<String> FORGE_PART_KEYS = new HashSet<>(Arrays.asList(
            "model", "textures", "x", "y", "uvlock", "weight", "submodel", "transform", "custom",
            "smooth_lighting", "gui3d"));

    /**
     * The models and textures of Forge's own blockstate format, which files mark with "forge_marker": 1. A block's
     * look is put together from "defaults", a part for each property's value, and parts for whole states, each of
     * which can name a model, other models in "submodel", and textures to give them.
     */
    private void collectForgeParts(JsonObject json, List<String> modelNames) {
        if (json.has("defaults")) {
            collectForgePart(json.get("defaults"), modelNames);
        }
        if (!json.has("variants") || !json.get("variants").isJsonObject()) {
            return;
        }
        for (Map.Entry<String, JsonElement> entry : json.getAsJsonObject("variants").entrySet()) {
            JsonElement value = entry.getValue();
            if (isForgeWholeState(entry.getKey(), value)) {
                collectForgePart(value, modelNames);
            } else if (value.isJsonObject()) {
                for (Map.Entry<String, JsonElement> part : value.getAsJsonObject().entrySet()) {
                    collectForgePart(part.getValue(), modelNames);
                }
            }
        }
    }

    private static boolean isForgeWholeState(String key, JsonElement value) {
        if (value.isJsonArray()) {
            return true;
        }
        if (!value.isJsonObject()) {
            return false;
        }
        if (key.contains("=") || key.equals("normal") || key.equals("inventory")) {
            return true;
        }
        for (Map.Entry<String, JsonElement> entry : value.getAsJsonObject().entrySet()) {
            if (!entry.getValue().isJsonObject() || FORGE_PART_KEYS.contains(entry.getKey())) {
                return true;
            }
        }
        return false;
    }

    private void collectForgePart(JsonElement part, List<String> modelNames) {
        if (part.isJsonArray()) {
            for (JsonElement element : part.getAsJsonArray()) {
                collectForgePart(element, modelNames);
            }
            return;
        }
        if (!part.isJsonObject()) {
            return;
        }
        JsonObject object = part.getAsJsonObject();
        JsonElement model = object.get("model");
        if (model != null && model.isJsonPrimitive()) {
            modelNames.add(model.getAsString());
        }
        JsonElement textures = object.get("textures");
        if (textures != null && textures.isJsonObject()) {
            for (Map.Entry<String, JsonElement> texture : textures.getAsJsonObject().entrySet()) {
                JsonElement value = texture.getValue();
                if (value.isJsonPrimitive() && !value.getAsString().startsWith("#")) {
                    exportTexture(value.getAsString());
                }
            }
        }
        JsonElement submodel = object.get("submodel");
        if (submodel != null && submodel.isJsonPrimitive()) {
            modelNames.add(submodel.getAsString());
        } else if (submodel != null && submodel.isJsonObject()) {
            for (Map.Entry<String, JsonElement> sub : submodel.getAsJsonObject().entrySet()) {
                collectForgePart(sub.getValue(), modelNames);
            }
        }
    }

    private static void collectModels(JsonElement entry, List<String> names) {
        if (entry.isJsonArray()) {
            for (JsonElement element : entry.getAsJsonArray()) {
                collectModels(element, names);
            }
        } else if (entry.isJsonObject()) {
            JsonElement model = entry.getAsJsonObject().get("model");
            if (model != null) {
                names.add(model.getAsString());
            }
        }
    }

    private boolean[] readModel(String name) {
        String location = normalize(name);
        boolean[] known = models.get(location);
        if (known != null) {
            return known;
        }
        // Guards against a model that is its own ancestor.
        models.put(location, new boolean[2]);

        boolean[] info = new boolean[2];
        if (location != null && !location.contains(":builtin/")) {
            String file = file(location, "models/", ".json");
            JsonObject json = readJson(file);
            if (json != null) {
                copy(file);
                info = readModelJson(json);
            }
        }
        models.put(location, info);
        return info;
    }

    private boolean[] readModelJson(JsonObject json) {
        boolean hasElements = json.has("elements");
        boolean usesLoader = false;
        if (json.has("loader")) {
            String loader = normalize(json.get("loader").getAsString());
            if (COMPOSITE_LOADERS.contains(loader) && json.has("children")) {
                // Made of other models, each written out in full here. MCEdit draws them all.
                for (Map.Entry<String, JsonElement> child : json.getAsJsonObject("children").entrySet()) {
                    boolean[] part = readModelJson(child.getValue().getAsJsonObject());
                    hasElements |= part[0];
                    usesLoader |= part[1];
                }
            } else if (OBJ_LOADERS.contains(loader) && json.has("model")) {
                // An .obj file and its materials, which MCEdit draws.
                boolean found = exportObj(json.get("model").getAsString(), false);
                if (found && json.has("mtl_override")) {
                    exportMaterials(objFile(json.get("mtl_override").getAsString(), false));
                }
                hasElements |= found;
                usesLoader |= !found;
            } else if (!PLAIN_LOADERS.contains(loader)) {
                // A loader of a mod's own. Some name a model file, as HBM's do, which MCEdit draws as the loader
                // would: an .obj file with its materials, or a Collada .dae file.
                boolean found = json.has("model") && json.get("model").isJsonPrimitive()
                        && exportModelFile(json.get("model").getAsString());
                hasElements |= found;
                usesLoader |= !found;
            }
        }
        if (json.has("textures")) {
            for (Map.Entry<String, JsonElement> entry : json.getAsJsonObject("textures").entrySet()) {
                JsonElement value = entry.getValue();
                // From 26.1 a texture can be an object, with the texture in "sprite".
                if (value.isJsonObject() && value.getAsJsonObject().has("sprite")) {
                    value = value.getAsJsonObject().get("sprite");
                }
                if (value.isJsonPrimitive() && !value.getAsString().startsWith("#")) {
                    exportTexture(value.getAsString());
                }
            }
        }
        if (json.has("parent")) {
            boolean[] parent = readModel(json.get("parent").getAsString());
            hasElements |= parent[0];
            usesLoader |= parent[1];
        }
        return new boolean[]{hasElements, usesLoader};
    }

    /**
     * Copy an OBJ model, the material files it names and their textures. A name with the path from the namespace,
     * as "somemod:models/block/thing.obj", is the file itself; Forge 1.12 names it from models/, or from
     * models/block/ in a blockstate file of the game's kind. Returns whether the file was there.
     */
    private boolean exportObj(String name, boolean inBlockFolder) {
        String file = objFile(name, inBlockFolder);
        if (file == null || !exists(file)) {
            String other = objFile(name, !inBlockFolder);
            if (other == null || !exists(other)) {
                report.add("OBJ model not found: " + name);
                return false;
            }
            file = other;
        }
        copy(file);
        String text = readText(file);
        if (text != null) {
            for (String line : text.split("\\r?\\n")) {
                line = line.trim();
                if (line.startsWith("mtllib ")) {
                    String library = line.substring("mtllib ".length()).trim();
                    if (library.contains(":")) {
                        exportMaterials(objFile(library, false));
                    } else {
                        int slash = file.lastIndexOf('/');
                        exportMaterials(file.substring(0, slash + 1) + library);
                    }
                }
            }
        }
        return true;
    }

    /**
     * Copy the model file a mod's own loader names, by itself or with ".obj" or ".dae" after it, from the namespace
     * or from models/. Returns whether there was one.
     */
    private boolean exportModelFile(String name) {
        List<String> names = name.endsWith(".obj") || name.endsWith(".dae")
                ? Collections.singletonList(name) : Arrays.asList(name + ".obj", name + ".dae");
        for (String candidate : names) {
            for (boolean inBlockFolder : new boolean[]{false, true}) {
                String file = objFile(candidate, inBlockFolder);
                if (file == null || !exists(file)) {
                    continue;
                }
                if (candidate.endsWith(".obj")) {
                    return exportObj(candidate, inBlockFolder);
                }
                copy(file);
                return true;
            }
        }
        return false;
    }

    private String objFile(String name, boolean inBlockFolder) {
        String location = normalize(name);
        if (location == null) {
            return null;
        }
        int colon = location.indexOf(':');
        String path = location.substring(colon + 1);
        if (path.startsWith("models/")) {
            return location;
        }
        return location.substring(0, colon + 1) + "models/" + (inBlockFolder ? "block/" : "") + path;
    }

    /** Copy an .mtl file and the textures of its materials, but those given by the model, as "#all". */
    private void exportMaterials(String file) {
        if (file == null || !exists(file)) {
            report.add("OBJ materials not found: " + file);
            return;
        }
        copy(file);
        String text = readText(file);
        if (text == null) {
            return;
        }
        for (String line : text.split("\\r?\\n")) {
            line = line.trim();
            if (line.startsWith("map_Kd ")) {
                String[] words = line.split("\\s+");
                String texture = words[words.length - 1];
                if (!texture.startsWith("#")) {
                    exportTexture(texture);
                }
            }
        }
    }

    private String readText(String file) {
        try (InputStream stream = game.openResource(file)) {
            return stream == null ? null : new String(readAll(stream), StandardCharsets.UTF_8);
        } catch (IOException | RuntimeException e) {
            report.add("Could not read " + file + ": " + e);
            return null;
        }
    }

    /**
     * Write a model of faces the game drew, as MCEdit's "mcedit:quads" loader reads them, and copy their textures.
     * Alike states share one file. Returns the model's name as this version's blockstate files name models.
     */
    String writeGeneratedModel(String blockId, List<Quad> quads) {
        Map<String, String> keys = new LinkedHashMap<>();
        JsonArray faces = new JsonArray();
        for (Quad quad : quads) {
            String key = keys.get(quad.texture);
            if (key == null) {
                key = String.valueOf(keys.size());
                keys.put(quad.texture, key);
            }
            faces.add(quad.toJson(key));
        }
        JsonObject textures = new JsonObject();
        for (Map.Entry<String, String> entry : keys.entrySet()) {
            textures.addProperty(entry.getValue(), entry.getKey());
            if (entry.getKey().endsWith(".png")) {
                copy(entry.getKey());  // a texture file of its own, as an entity's
            } else {
                exportTexture(entry.getKey());
            }
        }
        JsonObject json = new JsonObject();
        json.addProperty("loader", "mcedit:quads");
        json.add("textures", textures);
        json.add("quads", faces);
        byte[] bytes = json.toString().getBytes(StandardCharsets.UTF_8);

        int colon = blockId.indexOf(':');
        // The game finds files with the namespace in lower case, which the block's name may not have before 1.13.
        String namespace = blockId.substring(0, colon).toLowerCase(java.util.Locale.ROOT);
        String name = namespace + ":mcedit_generated/" + blockId.substring(colon + 1) + "/" + digest(bytes);
        // Blockstate files name models from models/block/ before 1.13, and from models/ after.
        String path = (game.blockstateModelsInBlockFolder() ? "models/block/" : "models/") + name.substring(colon + 1);
        if (generated.add(path)) {
            try {
                PackWriter.writeAsset(folder, "assets/" + namespace + "/" + path + ".json", bytes);
            } catch (IOException e) {
                throw new java.io.UncheckedIOException(e);
            }
        }
        return name;
    }

    private final Set<String> generated = new HashSet<>();
    private final Set<String> exportedTextures = new HashSet<>();

    private static String digest(byte[] bytes) {
        try {
            byte[] hash = MessageDigest.getInstance("SHA-1").digest(bytes);
            StringBuilder text = new StringBuilder();
            for (int i = 0; i < 6; i++) {
                text.append(String.format("%02x", hash[i]));
            }
            return text.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    /** Whether a texture, as a sprite's "namespace:path" or a file's "namespace:textures/....png", is there. */
    boolean textureExists(String name) {
        String texture = name.indexOf(':') < 0 ? "minecraft:" + name : name;
        return exists(texture.endsWith(".png") ? texture : file(texture, "textures/", ".png"));
    }

    /** Copy a resource as it is, unless it is the game's own. */
    void copyResource(String name) {
        copy(name);
    }

    /** Copy a texture, with the file that says how it animates if it has one. */
    void exportTexture(String name) {
        String texture = normalize(name);
        if (texture == null || texture.endsWith(":missingno") || !exportedTextures.add(texture)) {
            return;
        }
        String file = file(texture, "textures/", ".png");
        if (!exists(file)) {
            report.add("Texture not found: " + file);
            return;
        }
        copy(file);
        copy(file + ".mcmeta");
        exportConnectedTextures(file + ".mcmeta");
    }

    /**
     * The CTM mod names the textures a connected texture is made of in its base texture's .mcmeta file, in "ctm":
     * {"textures": [...], "proxy": ...}. They are copied with it.
     */
    private void exportConnectedTextures(String mcmeta) {
        if (!exists(mcmeta)) {
            return;
        }
        JsonObject json = readJson(mcmeta);
        if (json == null || !json.has("ctm") || !json.get("ctm").isJsonObject()) {
            return;
        }
        JsonObject ctm = json.getAsJsonObject("ctm");
        if (ctm.has("textures") && ctm.get("textures").isJsonArray()) {
            for (JsonElement texture : ctm.getAsJsonArray("textures")) {
                if (texture.isJsonPrimitive()) {
                    exportTexture(texture.getAsString());
                }
            }
        }
        if (ctm.has("proxy") && ctm.get("proxy").isJsonPrimitive()) {
            exportTexture(ctm.get("proxy").getAsString());
        }
    }

    /** "somemod:block/thing" in "blockstates/" with ".json" is "somemod:blockstates/block/thing.json" */
    private static String file(String location, String folder, String extension) {
        int colon = location.indexOf(':');
        return location.substring(0, colon + 1) + folder + location.substring(colon + 1) + extension;
    }

    private String normalize(String name) {
        if (name == null || name.isEmpty()) {
            report.add("Bad resource name: \"" + name + "\"");
            return null;
        }
        return name.indexOf(':') < 0 ? "minecraft:" + name : name;
    }

    private boolean exists(String file) {
        try (InputStream stream = game.openResource(file)) {
            return stream != null;
        } catch (IOException | RuntimeException e) {
            return false;
        }
    }

    private JsonObject readJson(String file) {
        try (InputStream stream = game.openResource(file)) {
            if (stream == null) {
                return null;
            }
            return new JsonParser().parse(new InputStreamReader(stream, StandardCharsets.UTF_8)).getAsJsonObject();
        } catch (IOException | RuntimeException e) {
            report.add("Could not read " + file + ": " + e);
            return null;
        }
    }

    private void copy(String file) {
        if (!copied.add(file) || game.isGameResource(file)) {
            return;
        }
        try (InputStream stream = game.openResource(file)) {
            if (stream == null) {
                return;
            }
            int colon = file.indexOf(':');
            PackWriter.writeAsset(folder, "assets/" + file.substring(0, colon) + "/" + file.substring(colon + 1),
                    readAll(stream));
        } catch (IOException | RuntimeException e) {
            report.add("Could not copy " + file + ": " + e);
        }
    }

    static byte[] readAll(InputStream stream) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int read;
        while ((read = stream.read(buffer)) > 0) {
            out.write(buffer, 0, read);
        }
        return out.toByteArray();
    }
}
