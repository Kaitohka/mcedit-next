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
import net.minecraft.block.Block;
import net.minecraft.block.state.IBlockState;
import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.BlockModelShapes;
import net.minecraft.client.renderer.model.BakedQuad;
import net.minecraft.client.renderer.model.IBakedModel;
import net.minecraft.client.renderer.model.ModelResourceLocation;
import net.minecraft.client.renderer.tileentity.TileEntityRendererDispatcher;
import net.minecraft.fluid.IFluidState;
import net.minecraft.init.Blocks;
import net.minecraft.init.Fluids;
import net.minecraft.nbt.NBTTagCompound;
import net.minecraft.resources.IResource;
import net.minecraft.state.IProperty;
import net.minecraft.tileentity.TileEntity;
import net.minecraft.tileentity.TileEntityType;
import net.minecraft.util.BlockRenderLayer;
import net.minecraft.util.EnumBlockRenderType;
import net.minecraft.util.EnumFacing;
import net.minecraft.util.ResourceLocation;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.registry.IRegistry;
import net.minecraft.world.IBlockReader;
import net.minecraft.world.IWorldReaderBase;
import net.minecraft.world.biome.Biome;
import net.minecraftforge.versions.mcp.MCPVersion;

import java.io.FileNotFoundException;
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
 * Reads blocks from Minecraft 1.13.2, in Forge. Mojang published no names for this version, so all of the game's
 * names here are MCP's. The mod loader's part is in Glue.
 */
abstract class VersionGame implements Game {
    // MCEdit's numbering, which comes from the render types of Minecraft 1.12.
    private static final int RENDER_INVISIBLE = 0;
    private static final int RENDER_LIQUID = 1;
    private static final int RENDER_ENTITY = 2;
    private static final int RENDER_MODEL = 3;

    /** The data version of 1.13.2, which this version of the game does not say in code. */
    private static final int DATA_VERSION = 1631;

    /** A world with nothing in it, to ask blocks about themselves. */
    private static final IBlockReader NO_WORLD = new IBlockReader() {
        @Override
        public TileEntity getTileEntity(BlockPos pos) {
            return null;
        }

        @Override
        public IBlockState getBlockState(BlockPos pos) {
            return Blocks.AIR.getDefaultState();
        }

        @Override
        public IFluidState getFluidState(BlockPos pos) {
            return Fluids.EMPTY.getDefaultState();
        }
    };
    private static final BlockPos ORIGIN = BlockPos.ORIGIN;

    private final Random random = new Random(42);

    private static Minecraft minecraft() {
        return Minecraft.getInstance();
    }

    @Override
    public String minecraftVersion() {
        return MCPVersion.getMCVersion();
    }

    @Override
    public int dataVersion() {
        return DATA_VERSION;
    }

    @Override
    public List<String> blockIds() {
        List<String> ids = new ArrayList<>();
        for (ResourceLocation id : IRegistry.BLOCK.keySet()) {
            ids.add(id.toString());
        }
        return ids;
    }

    @Override
    public BlockFacts readBlock(String name, Report report) {
        Block block = IRegistry.BLOCK.get(new ResourceLocation(name));
        Map<String, List<String>> properties = new LinkedHashMap<>();
        for (IProperty<?> property : block.getStateContainer().getProperties()) {
            properties.put(property.getName(), valueNames(property));
        }
        List<StateFacts> states = new ArrayList<>();
        for (IBlockState state : block.getStateContainer().getValidStates()) {
            states.add(readState(name, state, report));
        }
        String displayName = report.guarded(name, "displayName", () -> block.getNameTextComponent().getString(), name);
        return new BlockFacts(name, displayName, block.getTranslationKey(), stateName(block.getDefaultState()),
                properties, states);
    }

    private StateFacts readState(String id, IBlockState state, Report report) {
        Map<String, JsonElement> a = new LinkedHashMap<>();
        a.put("opaqueCube", json(report.guarded(id, "opaqueCube", () -> state.isOpaqueCube(NO_WORLD, ORIGIN), false)));
        a.put("normalCube", json(report.guarded(id, "normalCube", state::isNormalCube, false)));
        a.put("collidable", json(report.guarded(id, "collidable", () -> !state.getCollisionShape(NO_WORLD, ORIGIN).isEmpty(), true)));
        a.put("opacity", json(report.guarded(id, "opacity", () -> state.getOpacity(NO_WORLD, ORIGIN), 15)));
        a.put("brightness", json(report.guarded(id, "brightness", state::getLightValue, 0)));
        a.put("useNeighborBrightness", json(report.guarded(id, "useNeighborBrightness",
                () -> state.useNeighborBrightness(NO_WORLD, ORIGIN), false)));
        a.put("hasEntity", json(report.guarded(id, "hasEntity", () -> state.getBlock().hasTileEntity(state), false)));
        a.put("materialLiquid", json(state.getMaterial().isLiquid()));
        a.put("materialMapColor", json(report.guarded(id, "materialMapColor",
                () -> state.getMaterialColor(NO_WORLD, ORIGIN).colorValue, 0)));
        a.put("renderLayer", new JsonPrimitive(report.guarded(id, "renderLayer", () -> renderLayer(state), "unknown")));
        a.put("renderType", json(renderType(state)));

        IBakedModel model = report.guarded(id, "model",
                () -> minecraft().getBlockRendererDispatcher().getModelForState(state), null);
        String particle = "";
        if (model != null) {
            particle = report.guarded(id, "particleTexture", () -> model.getParticleTexture().getName().toString(), "");
            boolean tinted = report.guarded(id, "tinted", () -> isTinted(model, state), false);
            a.put("tinted", json(tinted));
            if (tinted) {
                // Without a world, a biome-dependent color provider returns its default color.
                a.put("tintColor", json(report.guarded(id, "tintColor",
                        () -> minecraft().getBlockColors().getColor(state, (IWorldReaderBase) null, null, 0), -1)));
            }
        }

        ModelResourceLocation location = BlockModelShapes.getModelLocation(state);
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
    private static List<Quad> modelQuads(IBakedModel model, IBlockState state) {
        List<Quad> quads = modelQuadsOf(model, state);
        return quads.isEmpty() && state != null ? modelQuadsOf(model, null) : quads;
    }

    private static List<Quad> modelQuadsOf(IBakedModel model, IBlockState state) {
        List<Quad> quads = new ArrayList<>();
        for (EnumFacing side : EnumFacing.values()) {
            for (BakedQuad quad : model.getQuads(state, side, new Random(42))) {
                quads.add(quad(quad, side.getName()));
            }
        }
        for (BakedQuad quad : model.getQuads(state, null, new Random(42))) {
            quads.add(quad(quad, null));
        }
        return quads;
    }

    private static Quad quad(BakedQuad quad, String cullface) {
        TextureAtlasSprite sprite = quad.getSprite();
        return Quad.fromVertexData(quad.getVertexData(), sprite.getName().toString(), sprite.getMinU(), sprite.getMaxU(),
                sprite.getMinV(), sprite.getMaxV(), quad.getFace().getName(), cullface, quad.getTintIndex(), true);
    }

    /** "stone" has no namespace before 1.13, and is the game's own. */
    private static String withNamespace(String name) {
        return name.indexOf(':') < 0 ? "minecraft:" + name : name;
    }

    private boolean isTinted(IBakedModel model, IBlockState state) {
        for (EnumFacing side : EnumFacing.values()) {
            if (anyTinted(model.getQuads(state, side, random))) {
                return true;
            }
        }
        return anyTinted(model.getQuads(state, null, random));
    }

    private static boolean anyTinted(List<BakedQuad> quads) {
        for (BakedQuad quad : quads) {
            if (quad.hasTintIndex()) {
                return true;
            }
        }
        return false;
    }

    private static int renderType(IBlockState state) {
        EnumBlockRenderType type = state.getRenderType();
        if (type == EnumBlockRenderType.MODEL) {
            return RENDER_MODEL;
        }
        if (type == EnumBlockRenderType.ENTITYBLOCK_ANIMATED) {
            return RENDER_ENTITY;
        }
        // Since 1.13, liquids have no render type of their own: the fluid is drawn separately.
        return state.getMaterial().isLiquid() ? RENDER_LIQUID : RENDER_INVISIBLE;
    }

    /** In this version the layer is the block's, the same for all of its states. */
    private static String renderLayer(IBlockState state) {
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

    /**
     * Block entity types don't say which blocks they are for in this version, so each block that has one is asked
     * to make it.
     */
    @Override
    public List<PackWriter.BlockEntity> readBlockEntities(Predicate<String> include, Report report) {
        Map<TileEntityType<?>, List<String>> blocksByType = new LinkedHashMap<>();
        for (ResourceLocation blockId : IRegistry.BLOCK.keySet()) {
            Block block = IRegistry.BLOCK.get(blockId);
            IBlockState state = block.getDefaultState();
            if (!report.guarded(blockId.toString(), "hasEntity",
                    () -> block.hasTileEntity(state), false)) {
                continue;
            }
            TileEntity instance = report.guarded(blockId.toString(), "its block entity",
                    () -> block.createTileEntity(state, NO_WORLD), null);
            if (instance != null && instance.getType() != null) {
                blocksByType.computeIfAbsent(instance.getType(), type -> new ArrayList<>())
                        .add(blockId.toString());
            }
        }
        List<PackWriter.BlockEntity> entities = new ArrayList<>();
        for (ResourceLocation key : IRegistry.BLOCK_ENTITY_TYPE.keySet()) {
            TileEntityType<?> type = IRegistry.BLOCK_ENTITY_TYPE.get(key);
            if (!include.test(key.toString())) {
                continue;
            }
            String id = key.toString();
            List<String> blocks = blocksByType.getOrDefault(type, new ArrayList<>());
            blocks.sort(null);
            Boolean hasRenderer = null;
            String defaultNbt = null;
            if (!blocks.isEmpty()) {
                TileEntity instance = report.guarded(id, "a new block entity", type::create, null);
                if (instance != null) {
                    hasRenderer = report.guarded(id, "whether it has a renderer",
                            () -> TileEntityRendererDispatcher.instance.getRenderer(instance) != null, null);
                    defaultNbt = report.guarded(id, "its default NBT",
                            () -> instance.write(new NBTTagCompound()).toString(), null);
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
        for (ResourceLocation key : IRegistry.BIOME.keySet()) {
            Biome biome = IRegistry.BIOME.get(key);
            if (!include.test(key.toString())) {
                continue;
            }
            String id = key.toString();
            JsonObject json = report.guarded(id, "the biome", () -> {
                JsonObject climate = new JsonObject();
                climate.addProperty("temperature", biome.getDefaultTemperature());
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
    public InputStream openResource(String name) throws IOException {
        try {
            return minecraft().getResourceManager().getResource(new ResourceLocation(name)).getInputStream();
        } catch (FileNotFoundException e) {
            return null;
        }
    }

    @Override
    public boolean isGameResource(String name) {
        try (IResource resource = minecraft().getResourceManager().getResource(new ResourceLocation(name))) {
            String pack = resource.getPackName();
            return "default".equalsIgnoreCase(pack) || "vanilla".equals(pack);
        } catch (IOException e) {
            return false;
        }
    }

    /** The canonical name of a state: properties sorted by name, or empty if it has none. */
    private static String stateName(IBlockState state) {
        List<IProperty<?>> properties = new ArrayList<>(state.getProperties());
        properties.sort(Comparator.comparing(IProperty::getName));
        return properties.stream()
                .map(property -> property.getName() + "=" + valueName(state, property))
                .collect(Collectors.joining(","));
    }

    private static <T extends Comparable<T>> String valueName(IBlockState state, IProperty<T> property) {
        return property.getName(state.get(property));
    }

    private static <T extends Comparable<T>> List<String> valueNames(IProperty<T> property) {
        return property.getAllowedValues().stream().map(property::getName).collect(Collectors.toList());
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
    private List<Quad> entityQuads(Block block, IBlockState state) {
        TileEntity entity = block.createTileEntity(state, NO_WORLD);
        if (entity == null) {
            return null;
        }
        TileEntityRenderer<TileEntity> renderer = TileEntityRendererDispatcher.instance.getRenderer(entity);
        if (renderer == null) {
            return null;
        }
        // The block and state the renderer asks the block entity for, which would come from a world.
        FeedbackCapture.setFieldOfType(entity, TileEntity.class, IBlockState.class, state);
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
            for (Object value : FeedbackCapture.mapValues(minecraft().getTextureMap(), TextureAtlasSprite.class)) {
                TextureAtlasSprite sprite = (TextureAtlasSprite) value;
                feedbackSprites.add(new FeedbackCapture.Sprite(sprite.getName().toString(), sprite.getMinU(), sprite.getMaxU(),
                        sprite.getMinV(), sprite.getMaxV()));
            }
        }
        FeedbackCapture capture = new FeedbackCapture();
        TextureManager real = minecraft().getTextureManager();
        // Before a world is drawn, the renderers have no texture manager or font to draw with.
        FeedbackCapture.fillEmptyFields(TileEntityRendererDispatcher.instance, minecraft());
        Runnable restore = FeedbackCapture.swap(real, new MarkingTextures(real, capture), minecraft(), TileEntityRendererDispatcher.instance);
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
        public void bindTexture(ResourceLocation location) {
            real.bindTexture(location);
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
            Tessellator.getInstance().draw();
        } catch (RuntimeException ignored) {
            // it was not drawing
        }
    }
}
