package net.mcedit.exporter.neoforge;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.mcedit.exporter.pack.PackWriter;
import net.minecraft.ResourceLocationException;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.packs.resources.Resource;
import net.minecraft.server.packs.resources.ResourceManager;

import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/**
 * Copies the blockstate files, models and textures that blocks use into a block pack, following each file's
 * references so that only those in use are copied. Reading through the resource manager means that resource packs
 * in use are taken into account. Files from the game itself are left out, as MCEdit reads those from its jar.
 *
 * Also works out whether MCEdit will be able to draw a block from these files. One failure never stops the export:
 * it goes into the pack's report.
 */
final class AssetExporter {
    /** The game's own resource pack, whose files MCEdit already has. */
    private static final String VANILLA_PACK_ID = "vanilla";
    /** NeoForge's loader for a model made of other models, which MCEdit can read. */
    private static final String COMPOSITE_LOADER = "neoforge:composite";

    private final ResourceManager resources;
    private final Path folder;
    private final List<String> problems;
    private final Set<ResourceLocation> copied = new HashSet<>();
    private final Map<ResourceLocation, ModelInfo> models = new HashMap<>();
    private final Map<ResourceLocation, String> blockstateSources = new HashMap<>();

    /**
     * @param hasElements  whether the model or a parent has elements, which are what MCEdit draws
     * @param usesLoader   whether the model or a parent needs a custom model loader that MCEdit doesn't have
     */
    private record ModelInfo(boolean hasElements, boolean usesLoader) {
    }

    AssetExporter(ResourceManager resources, Path folder, List<String> problems) {
        this.resources = resources;
        this.folder = folder;
        this.problems = problems;
    }

    /**
     * Copy the files for a block, and return where MCEdit should get its appearance from: "json" for its model
     * files, or "fallback" for its particle texture on a plain cube. The result is the same for all states
     * of a block, to keep this simple: one part that can't be read makes the whole block use the fallback.
     */
    String exportBlockstate(ResourceLocation block) {
        return blockstateSources.computeIfAbsent(block, this::readBlockstate);
    }

    private String readBlockstate(ResourceLocation block) {
        ResourceLocation file = ResourceLocation.fromNamespaceAndPath(block.getNamespace(),
                "blockstates/" + block.getPath() + ".json");
        JsonObject json = readJson(file);
        if (json == null) {
            return "fallback";
        }
        copy(file);

        List<String> modelNames = new ArrayList<>();
        if (json.has("variants")) {
            json.getAsJsonObject("variants").entrySet().forEach(entry -> collectModels(entry.getValue(), modelNames));
        }
        if (json.has("multipart")) {
            json.getAsJsonArray("multipart").forEach(part -> {
                JsonElement apply = part.getAsJsonObject().get("apply");
                if (apply != null) {
                    collectModels(apply, modelNames);
                }
            });
        }

        boolean anyElements = false;
        boolean anyLoader = false;
        for (String name : modelNames) {
            ModelInfo model = readModel(parse(name));
            anyElements |= model.hasElements();
            anyLoader |= model.usesLoader();
        }
        return anyElements && !anyLoader ? "json" : "fallback";
    }

    private static void collectModels(JsonElement entry, List<String> names) {
        if (entry.isJsonArray()) {
            entry.getAsJsonArray().forEach(element -> collectModels(element, names));
        } else {
            JsonElement model = entry.getAsJsonObject().get("model");
            if (model != null) {
                names.add(model.getAsString());
            }
        }
    }

    private ModelInfo readModel(ResourceLocation name) {
        ModelInfo known = models.get(name);
        if (known != null) {
            return known;
        }
        // Guards against a model that is its own ancestor.
        models.put(name, new ModelInfo(false, false));

        ModelInfo info = new ModelInfo(false, false);
        if (name != null && !name.getPath().startsWith("builtin/")) {
            ResourceLocation file = ResourceLocation.fromNamespaceAndPath(name.getNamespace(),
                    "models/" + name.getPath() + ".json");
            JsonObject json = readJson(file);
            if (json != null) {
                copy(file);
                info = readModelJson(json);
            }
        }
        models.put(name, info);
        return info;
    }

    /** Copy what a model uses, its parents and the models it is made of, and find out what MCEdit can draw of it. */
    private ModelInfo readModelJson(JsonObject json) {
        boolean hasElements = json.has("elements");
        boolean usesLoader = false;
        if (json.has("loader")) {
            if (COMPOSITE_LOADER.equals(json.get("loader").getAsString()) && json.has("children")) {
                // Made of other models, each written out in full here. MCEdit draws them all.
                for (Map.Entry<String, JsonElement> child : json.getAsJsonObject("children").entrySet()) {
                    ModelInfo part = readModelJson(child.getValue().getAsJsonObject());
                    hasElements |= part.hasElements();
                    usesLoader |= part.usesLoader();
                }
            } else {
                usesLoader = true;
            }
        }
        if (json.has("textures")) {
            json.getAsJsonObject("textures").entrySet().forEach(entry -> {
                String value = entry.getValue().getAsString();
                if (!value.startsWith("#")) {
                    exportTexture(value);
                }
            });
        }
        if (json.has("parent")) {
            ModelInfo parent = readModel(parse(json.get("parent").getAsString()));
            hasElements |= parent.hasElements();
            usesLoader |= parent.usesLoader();
        }
        return new ModelInfo(hasElements, usesLoader);
    }

    /** Copy a texture, with the file that says how it animates if it has one. */
    void exportTexture(String name) {
        ResourceLocation texture = parse(name);
        if (texture == null || texture.getPath().equals("missingno")) {
            return;
        }
        ResourceLocation file = ResourceLocation.fromNamespaceAndPath(texture.getNamespace(),
                "textures/" + texture.getPath() + ".png");
        if (resources.getResource(file).isEmpty()) {
            problems.add("Texture not found: " + file);
            return;
        }
        copy(file);
        copy(ResourceLocation.fromNamespaceAndPath(file.getNamespace(), file.getPath() + ".mcmeta"));
    }

    private ResourceLocation parse(String name) {
        try {
            return ResourceLocation.parse(name);
        } catch (ResourceLocationException e) {
            problems.add("Bad resource name: " + name);
            return null;
        }
    }

    private JsonObject readJson(ResourceLocation file) {
        Optional<Resource> resource = resources.getResource(file);
        if (resource.isEmpty()) {
            return null;
        }
        try (InputStream stream = resource.get().open()) {
            return JsonParser.parseReader(new InputStreamReader(stream, StandardCharsets.UTF_8)).getAsJsonObject();
        } catch (IOException | RuntimeException e) {
            problems.add("Could not read " + file + ": " + e);
            return null;
        }
    }

    private void copy(ResourceLocation file) {
        if (!copied.add(file)) {
            return;
        }
        Optional<Resource> resource = resources.getResource(file);
        if (resource.isEmpty() || resource.get().sourcePackId().equals(VANILLA_PACK_ID)) {
            return;
        }
        try (InputStream stream = resource.get().open()) {
            PackWriter.writeAsset(folder, "assets/" + file.getNamespace() + "/" + file.getPath(), stream.readAllBytes());
        } catch (IOException | RuntimeException e) {
            problems.add("Could not copy " + file + ": " + e);
        }
    }
}
