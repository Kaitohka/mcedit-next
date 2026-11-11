package @PACKAGE@;

import net.minecraft.client.renderer.Tessellator;
import net.mcedit.exporter.export.FeedbackCapture;
import net.minecraft.client.renderer.texture.ITextureObject;
import net.minecraft.client.renderer.texture.TextureManager;
import net.minecraft.client.renderer.tileentity.TileEntityRenderer;

import net.mcedit.exporter.export.Quad;
import net.minecraft.client.renderer.texture.TextureAtlasSprite;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import net.mcedit.exporter.export.Game;
import net.mcedit.exporter.export.Report;
import net.mcedit.exporter.pack.PackWriter;
import net.minecraft.util.SharedConstants;
import net.minecraft.client.Minecraft;
import net.minecraft.util.BlockRenderLayer;
import net.minecraft.client.renderer.BlockModelShapes;
import net.minecraft.client.renderer.model.BakedQuad;
import net.minecraft.client.renderer.model.IBakedModel;
import net.minecraft.client.renderer.model.ModelResourceLocation;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.Direction;
import net.minecraft.util.registry.Registry;
import net.minecraft.client.renderer.tileentity.TileEntityRendererDispatcher;
import net.minecraft.nbt.CompoundNBT;
import net.minecraft.block.ITileEntityProvider;
import net.minecraft.util.ResourceLocation;
import net.minecraft.resources.IResource;
import net.minecraft.world.IBlockReader;
import net.minecraft.world.EmptyBlockReader;
import net.minecraft.world.biome.Biome;
import net.minecraft.block.Block;
import net.minecraft.block.BlockRenderType;
import net.minecraft.tileentity.TileEntity;
import net.minecraft.tileentity.TileEntityType;
import net.minecraft.block.BlockState;
import net.minecraft.state.IProperty;

import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.function.Predicate;
import java.util.stream.Collectors;

/**
 * Reads blocks from Minecraft 1.14.4, in Forge, which names the game's classes as MCP did and only their fields
 * and methods as Mojang does. Made from ../1.16.5/VersionGame.java by renaming the classes.
 */
abstract class VersionGame implements Game {
    // MCEdit's numbering, which comes from the render types of Minecraft 1.12.
    private static final int RENDER_INVISIBLE = 0;
    private static final int RENDER_LIQUID = 1;
    private static final int RENDER_ENTITY = 2;
    private static final int RENDER_MODEL = 3;

    private static final IBlockReader NO_WORLD = EmptyBlockReader.INSTANCE;
    private static final BlockPos ORIGIN = BlockPos.ZERO;
    private static final String VANILLA_PACK_ID = "vanilla";

    private final Minecraft minecraft = Minecraft.getInstance();
    private final Random random = new Random(42);

    @Override
    public String minecraftVersion() {
        return SharedConstants.getCurrentVersion().getName();
    }

    @Override
    public int dataVersion() {
        return SharedConstants.getCurrentVersion().getWorldVersion();
    }

    @Override
    public List<String> blockIds() {
        List<String> ids = new ArrayList<>();
        for (ResourceLocation id : Registry.BLOCK.keySet()) {
            ids.add(id.toString());
        }
        return ids;
    }

    @Override
    public BlockFacts readBlock(String name, Report report) {
        Block block = Registry.BLOCK.get(new ResourceLocation(name));
        Map<String, List<String>> properties = new LinkedHashMap<>();
        for (IProperty<?> property : block.getStateDefinition().getProperties()) {
            properties.put(property.getName(), valueNames(property));
        }
        List<StateFacts> states = new ArrayList<>();
        for (BlockState state : block.getStateDefinition().getPossibleStates()) {
            states.add(readState(name, state, report));
        }
        String displayName = report.guarded(name, "displayName", () -> block.getName().getString(), name);
        return new BlockFacts(name, displayName, block.getDescriptionId(), stateName(block.defaultBlockState()),
                properties, states);
    }

    private StateFacts readState(String id, BlockState state, Report report) {
        Map<String, JsonElement> a = new LinkedHashMap<>();
        a.put("opaqueCube", json(report.guarded(id, "opaqueCube", () -> state.isSolidRender(NO_WORLD, ORIGIN), false)));
        a.put("normalCube", json(report.guarded(id, "normalCube", () -> state.isCollisionShapeFullBlock(NO_WORLD, ORIGIN), false)));
        a.put("collidable", json(report.guarded(id, "collidable", () -> !state.getCollisionShape(NO_WORLD, ORIGIN).isEmpty(), true)));
        a.put("opacity", json(report.guarded(id, "opacity", () -> state.getLightBlock(NO_WORLD, ORIGIN), 15)));
        a.put("brightness", json(report.guarded(id, "brightness", state::getLightEmission, 0)));
        a.put("useNeighborBrightness", json(report.guarded(id, "useNeighborBrightness", state::useShapeForLightOcclusion, false)));
        a.put("hasEntity", json(state.getBlock() instanceof ITileEntityProvider));
        a.put("materialLiquid", json(state.getMaterial().isLiquid()));
        a.put("materialMapColor", json(report.guarded(id, "materialMapColor", () -> state.getMapColor(NO_WORLD, ORIGIN).col, 0)));
        a.put("renderLayer", new JsonPrimitive(report.guarded(id, "renderLayer", () -> renderLayer(state), "unknown")));
        a.put("renderType", json(renderType(state)));
        a.put("cullsSameBlock", json(report.guarded(id, "cullsSameBlock", () -> state.skipRendering(state, Direction.NORTH), false)));

        IBakedModel model = report.guarded(id, "model", () -> minecraft.getBlockRenderer().getBlockModel(state), null);
        String particle = "";
        if (model != null) {
            particle = report.guarded(id, "particleTexture", () -> model.getParticleIcon().getName().toString(), "");
            boolean tinted = report.guarded(id, "tinted", () -> isTinted(model, state), false);
            a.put("tinted", json(tinted));
            if (tinted) {
                // Without a world, a biome-dependent color provider returns its default color.
                a.put("tintColor", json(report.guarded(id, "tintColor",
                        () -> minecraft.getBlockColors().getColor(state, null, null, 0), -1)));
            }
        }

        ModelResourceLocation location = BlockModelShapes.stateToModelLocation(state);
        StateFacts facts = new StateFacts(stateName(state), a, location.getNamespace() + ":" + location.getPath(),
                location.getVariant(), particle);
        if (model != null) {
            facts.modelQuads = () -> modelQuads(model, state);
            facts.customModel = Game.isModelFromMod(model);
        }
        if (report.guarded(id, "hasEntity", () -> state.getBlock().hasTileEntity(state), false)) {
            facts.entityQuads = () -> entityQuads(state.getBlock(), state);
        }
        return facts;
    }

    /**
     * The faces of a state's model, as the game draws them: those hidden by each side's neighbor, then the rest. A
     * model whose block a block entity renderer draws in the world may give its faces only for the item, with no
     * state, as HBM's machines do; then those are taken.
     */
    private List<Quad> modelQuads(IBakedModel model, BlockState state) {
        List<Quad> quads = modelQuadsOf(model, state);
        return quads.isEmpty() && state != null ? modelQuadsOf(model, null) : quads;
    }

    private List<Quad> modelQuadsOf(IBakedModel model, BlockState state) {
        List<Quad> quads = new ArrayList<>();
        for (Direction direction : Direction.values()) {
            for (BakedQuad quad : model.getQuads(state, direction, new Random(42))) {
                quads.add(quad(quad, direction.toString()));
            }
        }
        for (BakedQuad quad : model.getQuads(state, null, new Random(42))) {
            quads.add(quad(quad, null));
        }
        return quads;
    }

    private static Quad quad(BakedQuad quad, String cullface) {
        TextureAtlasSprite sprite = quad.getSprite();
        return Quad.fromVertexData(quad.getVertices(), sprite.getName().toString(), sprite.getU0(),
                sprite.getU1(), sprite.getV0(), sprite.getV1(), quad.getDirection().toString(), cullface,
                quad.getTintIndex(), true);
    }


    private boolean isTinted(IBakedModel model, BlockState state) {
        for (Direction direction : Direction.values()) {
            if (anyTinted(model.getQuads(state, direction, random))) {
                return true;
            }
        }
        return anyTinted(model.getQuads(state, null, random));
    }

    private static boolean anyTinted(List<BakedQuad> quads) {
        for (BakedQuad quad : quads) {
            if (quad.isTinted()) {
                return true;
            }
        }
        return false;
    }

    private static int renderType(BlockState state) {
        BlockRenderType shape = state.getRenderShape();
        if (shape == BlockRenderType.MODEL) {
            return RENDER_MODEL;
        }
        if (shape == BlockRenderType.ENTITYBLOCK_ANIMATED) {
            return RENDER_ENTITY;
        }
        // Since 1.13, liquids have no render shape of their own: the fluid is drawn separately.
        return state.getMaterial().isLiquid() ? RENDER_LIQUID : RENDER_INVISIBLE;
    }

    /** In this version the layer is the block's, the same for all of its states. */
    private static String renderLayer(BlockState state) {
        BlockRenderLayer layer = state.getBlock().getRenderLayer();
        switch (layer) {
            case SOLID:
                return "solid";
            case CUTOUT_MIPPED:
                return "cutout_mipped";
            case CUTOUT:
                return "cutout";
            case TRANSLUCENT:
                return "translucent";
            default:
                return "other";
        }
    }

    @Override
    public List<PackWriter.BlockEntity> readBlockEntities(Predicate<String> include, Report report) {
        List<Block> blocksWithEntities = new ArrayList<>();
        for (Block block : Registry.BLOCK) {
            if (block instanceof ITileEntityProvider) {
                blocksWithEntities.add(block);
            }
        }
        List<PackWriter.BlockEntity> entities = new ArrayList<>();
        for (TileEntityType<?> type : Registry.BLOCK_ENTITY_TYPE) {
            ResourceLocation key = Registry.BLOCK_ENTITY_TYPE.getKey(type);
            if (key == null || !include.test(key.toString())) {
                continue;
            }
            String id = key.toString();
            List<Block> valid = blocksWithEntities.stream()
                    .filter(type::isValid)
                    .collect(Collectors.toList());
            List<String> blocks = valid.stream()
                    .map(block -> Registry.BLOCK.getKey(block).toString())
                    .sorted()
                    .collect(Collectors.toList());
            Boolean hasRenderer = null;
            String defaultNbt = null;
            if (!valid.isEmpty()) {
                TileEntity instance = report.guarded(id, "a new block entity", type::create, null);
                if (instance != null) {
                    hasRenderer = report.guarded(id, "whether it has a renderer",
                            () -> TileEntityRendererDispatcher.instance.getRenderer(instance) != null, null);
                    defaultNbt = report.guarded(id, "its default NBT", () -> instance.save(new CompoundNBT()).toString(), null);
                }
            }
            entities.add(new PackWriter.BlockEntity(id, blocks, hasRenderer, defaultNbt));
        }
        return entities;
    }

    /**
     * Biomes are the game's own in this version, with their climate and water colors in code. Grass and leaves take
     * their colors from the climate.
     */
    @Override
    public JsonObject readBiomes(Predicate<String> include, Report report) {
        JsonObject biomes = new JsonObject();
        for (Biome biome : Registry.BIOME) {
            ResourceLocation key = Registry.BIOME.getKey(biome);
            if (key == null || !include.test(key.toString())) {
                continue;
            }
            String id = key.toString();
            JsonObject json = report.guarded(id, "the biome", () -> {
                JsonObject climate = new JsonObject();
                climate.addProperty("temperature", biome.getTemperature());
                climate.addProperty("downfall", biome.getDownfall());
                JsonObject effects = new JsonObject();
                effects.addProperty("water_color", biome.getWaterColor());
                effects.addProperty("water_fog_color", biome.getWaterFogColor());
                climate.add("effects", effects);
                return climate;
            }, null);
            if (json != null) {
                biomes.add(id, json);
            }
        }
        return biomes;
    }


    @Override
    public List<String> listResources(String folder) {
        // A collection of names before 1.19, and a map of them to their resources after.
        Object listed = minecraft.getResourceManager().listResources(folder, any -> true);
        Iterable<?> ids = listed instanceof Map ? ((Map<?, ?>) listed).keySet() : (Iterable<?>) listed;
        List<String> names = new ArrayList<>();
        for (Object id : ids) {
            names.add(id.toString());
        }
        return names;
    }

    @Override
    public InputStream openResource(String name) throws IOException {
        ResourceLocation location = new ResourceLocation(name);
        if (!minecraft.getResourceManager().hasResource(location)) {
            return null;
        }
        return minecraft.getResourceManager().getResource(location).getInputStream();
    }

    @Override
    public boolean isGameResource(String name) {
        ResourceLocation location = new ResourceLocation(name);
        if (!minecraft.getResourceManager().hasResource(location)) {
            return false;
        }
        try (IResource resource = minecraft.getResourceManager().getResource(location)) {
            String pack = resource.getSourceName();
            return pack.equals(VANILLA_PACK_ID) || pack.equals("Default");
        } catch (IOException e) {
            return false;
        }
    }

    /** The canonical name of a state: properties sorted by name, or empty if it has none. */
    private static String stateName(BlockState state) {
        return state.getProperties().stream()
                .sorted(Comparator.comparing(IProperty::getName))
                .map(property -> property.getName() + "=" + valueName(state, property))
                .collect(Collectors.joining(","));
    }

    private static <T extends Comparable<T>> String valueName(BlockState state, IProperty<T> property) {
        return property.getName(state.getValue(property));
    }

    private static <T extends Comparable<T>> List<String> valueNames(IProperty<T> property) {
        return property.getPossibleValues().stream().map(property::getName).collect(Collectors.toList());
    }

    private static JsonElement json(boolean value) {
        return new JsonPrimitive(value);
    }

    private static JsonElement json(int value) {
        return new JsonPrimitive(value);
    }

    /**
     * What the block entity renderer of a state draws, with a block entity made for it and nothing else. Renderers
     * in this version draw with OpenGL itself, so it is read back with OpenGL's feedback mode.
     */
    private List<Quad> entityQuads(Block block, BlockState state) {
        TileEntity entity = block.createTileEntity(state, NO_WORLD);
        if (entity == null) {
            return null;
        }
        TileEntityRenderer<TileEntity> renderer = TileEntityRendererDispatcher.instance.getRenderer(entity);
        if (renderer == null) {
            return null;
        }
        // The block and state the renderer asks the block entity for, which would come from a world.
        FeedbackCapture.setFieldOfType(entity, TileEntity.class, BlockState.class, state);
        return drawnByRenderer(() -> {
            try {
                renderer.render(entity, 0, 0, 0, 0, -1);
            } catch (RuntimeException e) {
                finishDrawing();
                throw e;
            }
        });
    }

    private List<FeedbackCapture.Sprite> feedbackSprites;

    private List<Quad> drawnByRenderer(Runnable draw) {
        if (feedbackSprites == null) {
            feedbackSprites = new ArrayList<>();
            for (Object value : FeedbackCapture.mapValues(minecraft.getTextureAtlas(), TextureAtlasSprite.class)) {
                TextureAtlasSprite sprite = (TextureAtlasSprite) value;
                feedbackSprites.add(new FeedbackCapture.Sprite(sprite.getName().toString(), sprite.getU0(), sprite.getU1(),
                        sprite.getV0(), sprite.getV1()));
            }
        }
        FeedbackCapture capture = new FeedbackCapture();
        TextureManager real = minecraft.getTextureManager();
        // Before a world is drawn, the renderers have no texture manager or font to draw with.
        FeedbackCapture.fillEmptyFields(TileEntityRendererDispatcher.instance, minecraft);
        Runnable restore = FeedbackCapture.swap(real, new MarkingTextures(real, capture), minecraft, TileEntityRendererDispatcher.instance);
        try {
            return FeedbackCapture.quads(capture.capture("minecraft:textures/atlas/blocks.png", draw), feedbackSprites);
        } finally {
            restore.run();
        }
    }

    /** Stands in for the game's texture manager, saying which texture each renderer binds. */
    private static final class MarkingTextures extends TextureManager {
        private final TextureManager real;
        private final FeedbackCapture capture;

        MarkingTextures(TextureManager real, FeedbackCapture capture) {
            super(Minecraft.getInstance().getResourceManager());
            this.real = real;
            this.capture = capture;
        }

        @Override
        public void bind(ResourceLocation location) {
            real.bind(location);
            capture.bound(location.toString());
        }

        @Override
        public ITextureObject getTexture(ResourceLocation location) {
            return real.getTexture(location);
        }
    }

    /** Finish what a renderer that failed partway left in the tessellator, which would make the next one fail. */
    private static void finishDrawing() {
        try {
            Tessellator.getInstance().end();
        } catch (RuntimeException ignored) {
            // it was not drawing
        }
    }
}
