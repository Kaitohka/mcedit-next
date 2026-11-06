package net.mcedit.exporter.neoforge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import net.mcedit.exporter.pack.PackWriter;
import net.minecraft.SharedConstants;
import net.minecraft.client.Minecraft;
import net.minecraft.client.color.block.BlockColors;
import net.minecraft.client.renderer.ItemBlockRenderTypes;
import net.minecraft.client.renderer.RenderType;
import net.minecraft.client.renderer.block.BlockModelShaper;
import net.minecraft.client.renderer.block.BlockRenderDispatcher;
import net.minecraft.client.resources.model.BakedModel;
import net.minecraft.client.resources.model.ModelResourceLocation;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.HolderLookup;
import net.minecraft.core.Registry;
import net.minecraft.core.RegistryAccess;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.packs.resources.ResourceManager;
import net.minecraft.util.RandomSource;
import net.minecraft.world.level.BlockGetter;
import net.minecraft.world.level.biome.Biome;
import net.minecraft.world.level.biome.BiomeSpecialEffects;
import net.minecraft.world.level.EmptyBlockGetter;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.RenderShape;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.block.entity.BlockEntityType;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.Property;
import net.neoforged.fml.ModList;
import net.neoforged.neoforge.client.model.data.ModelData;

import java.io.IOException;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Predicate;
import java.util.function.Supplier;
import java.util.stream.Collectors;

/**
 * Reads blocks from the running game and writes them as a block pack. Must run on the client thread
 * after resources have loaded. Blocks from mods can throw from any of these calls, since they have
 * no world to look at here, so each read is guarded and failures go into the pack's report.
 */
final class BlockDumper {
    enum Scope {
        MODS(id -> !id.getNamespace().equals("minecraft")),
        VANILLA(id -> id.getNamespace().equals("minecraft")),
        ALL(id -> true);

        final Predicate<ResourceLocation> filter;

        Scope(Predicate<ResourceLocation> filter) {
            this.filter = filter;
        }
    }

    // MCEdit's numbering, which comes from the render types of Minecraft 1.12.
    private static final int RENDER_INVISIBLE = 0;
    private static final int RENDER_LIQUID = 1;
    private static final int RENDER_ENTITY = 2;
    private static final int RENDER_MODEL = 3;

    private static final BlockGetter NO_WORLD = EmptyBlockGetter.INSTANCE;
    private static final BlockPos ORIGIN = BlockPos.ZERO;

    private final Minecraft minecraft = Minecraft.getInstance();
    private final ResourceManager resources = minecraft.getResourceManager();
    private final BlockRenderDispatcher renderer = minecraft.getBlockRenderer();
    private final BlockColors colors = minecraft.getBlockColors();
    private final RandomSource random = RandomSource.create(42);
    private final Map<ResourceLocation, Boolean> blockstateFiles = new LinkedHashMap<>();
    private final List<String> problems = new ArrayList<>();
    private AssetExporter assets;

    record Result(int blocks, int states, int problems) {
    }

    Result export(Path folder, Scope scope) throws IOException {
        assets = new AssetExporter(resources, folder, problems);
        List<PackWriter.Block> blocks = new ArrayList<>();
        int states = 0;
        for (Map.Entry<net.minecraft.resources.ResourceKey<Block>, Block> entry : BuiltInRegistries.BLOCK.entrySet()) {
            ResourceLocation id = entry.getKey().location();
            if (scope.filter.test(id)) {
                PackWriter.Block block = readBlock(id, entry.getValue());
                blocks.add(block);
                states += block.states().size();
            }
        }
        blocks.sort(Comparator.comparing(PackWriter.Block::id));

        PackWriter.write(folder, manifest(scope), blocks, readBlockEntities(scope), readBiomes(scope), problems);
        return new Result(blocks.size(), states, problems.size());
    }

    private PackWriter.Block readBlock(ResourceLocation id, Block block) {
        Map<String, List<String>> properties = new LinkedHashMap<>();
        for (Property<?> property : block.getStateDefinition().getProperties()) {
            properties.put(property.getName(), valueNames(property));
        }

        List<PackWriter.State> states = new ArrayList<>();
        for (BlockState state : block.getStateDefinition().getPossibleStates()) {
            states.add(new PackWriter.State(stateName(state), readState(id, block, state)));
        }

        String displayName = guarded(id, "displayName", () -> block.getName().getString(), id.toString());
        return new PackWriter.Block(id.toString(), displayName, block.getDescriptionId(),
                stateName(block.defaultBlockState()), properties, states);
    }

    private Map<String, JsonElement> readState(ResourceLocation id, Block block, BlockState state) {
        Map<String, JsonElement> a = new LinkedHashMap<>();
        a.put("opaqueCube", json(guarded(id, "opaqueCube", () -> state.isSolidRender(NO_WORLD, ORIGIN), false)));
        a.put("normalCube", json(guarded(id, "normalCube", () -> state.isCollisionShapeFullBlock(NO_WORLD, ORIGIN), false)));
        a.put("collidable", json(guarded(id, "collidable", () -> !state.getCollisionShape(NO_WORLD, ORIGIN).isEmpty(), true)));
        a.put("opacity", json(guarded(id, "opacity", () -> state.getLightBlock(NO_WORLD, ORIGIN), 15)));
        a.put("brightness", json(guarded(id, "brightness", state::getLightEmission, 0)));
        a.put("useNeighborBrightness", json(guarded(id, "useNeighborBrightness", state::useShapeForLightOcclusion, false)));
        a.put("hasEntity", json(state.hasBlockEntity()));
        a.put("materialLiquid", json(state.liquid()));
        a.put("materialMapColor", json(guarded(id, "materialMapColor", () -> state.getMapColor(NO_WORLD, ORIGIN).col, 0)));
        a.put("renderLayer", new JsonPrimitive(guarded(id, "renderLayer", () -> renderLayer(state), "unknown")));
        a.put("renderType", json(renderType(state)));

        ModelResourceLocation model = BlockModelShaper.stateToModelLocation(state);
        a.put("resourcePath", new JsonPrimitive(model.id().toString()));
        a.put("resourceVariant", new JsonPrimitive(model.getVariant()));
        a.put("hasBlockstateFile", json(hasBlockstateFile(model.id())));
        a.put("modelSource", new JsonPrimitive(guarded(id, "modelSource", () -> assets.exportBlockstate(model.id()), "fallback")));

        readModel(id, state, a);
        return a;
    }

    /** Facts from the baked model, for blocks whose appearance is not fully described by JSON. */
    private void readModel(ResourceLocation id, BlockState state, Map<String, JsonElement> attributes) {
        BakedModel model = renderer.getBlockModel(state);
        String particleTexture = guarded(id, "particleTexture",
                () -> model.getParticleIcon(ModelData.EMPTY).contents().name().toString(), "");
        attributes.put("particleTexture", new JsonPrimitive(particleTexture));
        if (!particleTexture.isEmpty()) {
            guarded(id, "particleTexture file", () -> {
                assets.exportTexture(particleTexture);
                return true;
            }, false);
        }

        boolean tinted = guarded(id, "tinted", () -> isTinted(model, state), false);
        attributes.put("tinted", json(tinted));
        if (tinted) {
            // Without a world, a biome-dependent color provider returns its default color.
            attributes.put("tintColor", json(guarded(id, "tintColor", () -> colors.getColor(state, null, null, 0), -1)));
        }
    }

    private boolean isTinted(BakedModel model, BlockState state) {
        for (Direction direction : Direction.values()) {
            if (anyTinted(model.getQuads(state, direction, random, ModelData.EMPTY, null))) {
                return true;
            }
        }
        return anyTinted(model.getQuads(state, null, random, ModelData.EMPTY, null));
    }

    private static boolean anyTinted(List<net.minecraft.client.renderer.block.model.BakedQuad> quads) {
        for (var quad : quads) {
            if (quad.isTinted()) {
                return true;
            }
        }
        return false;
    }

    private boolean hasBlockstateFile(ResourceLocation id) {
        return blockstateFiles.computeIfAbsent(id, key -> resources.getResource(
                ResourceLocation.fromNamespaceAndPath(key.getNamespace(), "blockstates/" + key.getPath() + ".json")
        ).isPresent());
    }

    private static int renderType(BlockState state) {
        RenderShape shape = state.getRenderShape();
        if (shape == RenderShape.MODEL) {
            return RENDER_MODEL;
        }
        if (shape == RenderShape.ENTITYBLOCK_ANIMATED) {
            return RENDER_ENTITY;
        }
        // Since 1.13, liquids have no render shape of their own: the fluid is drawn separately.
        return state.liquid() ? RENDER_LIQUID : RENDER_INVISIBLE;
    }

    private static String renderLayer(BlockState state) {
        RenderType type = ItemBlockRenderTypes.getChunkRenderType(state);
        if (type == RenderType.solid()) {
            return "solid";
        }
        if (type == RenderType.cutoutMipped()) {
            return "cutout_mipped";
        }
        if (type == RenderType.cutout()) {
            return "cutout";
        }
        if (type == RenderType.translucent()) {
            return "translucent";
        }
        if (type == RenderType.tripwire()) {
            return "tripwire";
        }
        return "other";
    }

    private List<PackWriter.BlockEntity> readBlockEntities(Scope scope) {
        List<Block> blocksWithEntities = BuiltInRegistries.BLOCK.stream()
                .filter(block -> block.defaultBlockState().hasBlockEntity())
                .toList();

        List<PackWriter.BlockEntity> entities = new ArrayList<>();
        for (BlockEntityType<?> type : BuiltInRegistries.BLOCK_ENTITY_TYPE) {
            ResourceLocation id = BuiltInRegistries.BLOCK_ENTITY_TYPE.getKey(type);
            if (id == null || !scope.filter.test(id)) {
                continue;
            }
            List<Block> valid = blocksWithEntities.stream()
                    .filter(block -> type.isValid(block.defaultBlockState()))
                    .toList();
            List<String> blocks = valid.stream()
                    .map(block -> BuiltInRegistries.BLOCK.getKey(block).toString())
                    .sorted()
                    .collect(Collectors.toList());

            Boolean hasRenderer = null;
            String defaultNbt = null;
            if (!valid.isEmpty()) {
                BlockEntity instance = guarded(id, "a new block entity",
                        () -> type.create(BlockPos.ZERO, valid.get(0).defaultBlockState()), null);
                if (instance != null) {
                    hasRenderer = guarded(id, "whether it has a renderer",
                            () -> minecraft.getBlockEntityRenderDispatcher().getRenderer(instance) != null, null);
                    defaultNbt = guarded(id, "its default NBT",
                            () -> instance.saveWithFullMetadata(registries()).toString(), null);
                }
            }
            entities.add(new PackWriter.BlockEntity(id.toString(), blocks, hasRenderer, defaultNbt));
        }
        entities.sort(Comparator.comparing(PackWriter.BlockEntity::id));
        return entities;
    }

    /** Registries to save with. Those of a world are complete, but the title screen only has the built-in ones. */
    private HolderLookup.Provider registries() {
        if (minecraft.level != null) {
            return minecraft.level.registryAccess();
        }
        return RegistryAccess.fromRegistryOfRegistries(BuiltInRegistries.REGISTRY);
    }

    /** Biomes come from data packs, so they are only known while in a world. */
    private JsonObject readBiomes(Scope scope) {
        if (minecraft.level == null) {
            return null;
        }
        Registry<Biome> registry = minecraft.level.registryAccess().registryOrThrow(Registries.BIOME);
        JsonObject biomes = new JsonObject();
        for (Map.Entry<net.minecraft.resources.ResourceKey<Biome>, Biome> entry : registry.entrySet()) {
            ResourceLocation id = entry.getKey().location();
            if (scope.filter.test(id)) {
                JsonObject biome = guarded(id, "biome", () -> biomeJson(entry.getValue()), null);
                if (biome != null) {
                    biomes.add(id.toString(), biome);
                }
            }
        }
        return biomes;
    }

    private static JsonObject biomeJson(Biome biome) {
        BiomeSpecialEffects effects = biome.getSpecialEffects();
        JsonObject effectsJson = new JsonObject();
        effectsJson.addProperty("water_color", effects.getWaterColor());
        effects.getGrassColorOverride().ifPresent(color -> effectsJson.addProperty("grass_color", color));
        effects.getFoliageColorOverride().ifPresent(color -> effectsJson.addProperty("foliage_color", color));
        effectsJson.addProperty("grass_color_modifier", effects.getGrassColorModifier().getSerializedName());

        JsonObject json = new JsonObject();
        json.addProperty("temperature", biome.getBaseTemperature());
        json.addProperty("downfall", biome.getModifiedClimateSettings().downfall());
        json.add("effects", effectsJson);
        return json;
    }

    private static JsonObject manifest(Scope scope) {
        JsonObject manifest = new JsonObject();
        manifest.addProperty("generatedAt", Instant.now().toString());
        manifest.addProperty("scope", scope.name().toLowerCase());
        manifest.addProperty("minecraftVersion", SharedConstants.getCurrentVersion().getName());
        manifest.addProperty("dataVersion", SharedConstants.getCurrentVersion().getDataVersion().getVersion());
        manifest.addProperty("loader", "neoforge");
        ModList.get().getModContainerById("neoforge").ifPresent(container ->
                manifest.addProperty("loaderVersion", container.getModInfo().getVersion().toString()));

        JsonArray mods = new JsonArray();
        ModList.get().getMods().stream()
                .sorted(Comparator.comparing(mod -> mod.getModId()))
                .forEach(mod -> {
                    JsonObject entry = new JsonObject();
                    entry.addProperty("id", mod.getModId());
                    entry.addProperty("version", mod.getVersion().toString());
                    mods.add(entry);
                });
        manifest.add("mods", mods);
        return manifest;
    }

    /** The canonical name of a state: properties sorted by name, or empty if it has none. */
    private static String stateName(BlockState state) {
        return state.getProperties().stream()
                .sorted(Comparator.comparing(Property::getName))
                .map(property -> property.getName() + "=" + valueName(state, property))
                .collect(Collectors.joining(","));
    }

    private static <T extends Comparable<T>> String valueName(BlockState state, Property<T> property) {
        return property.getName(state.getValue(property));
    }

    private static <T extends Comparable<T>> List<String> valueNames(Property<T> property) {
        return property.getPossibleValues().stream().map(property::getName).collect(Collectors.toList());
    }

    private <T> T guarded(ResourceLocation id, String what, Supplier<T> read, T fallback) {
        try {
            return read.get();
        } catch (Exception e) {
            String problem = id + ": could not read " + what + ": " + e;
            if (problems.size() < 5000 && !problems.contains(problem)) {
                problems.add(problem);
            }
            return fallback;
        }
    }

    private static JsonElement json(boolean value) {
        return new JsonPrimitive(value);
    }

    private static JsonElement json(int value) {
        return new JsonPrimitive(value);
    }
}
