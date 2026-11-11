package @PACKAGE@;

import net.minecraft.client.renderer.Tessellator;
import net.mcedit.exporter.export.FeedbackCapture;
import net.minecraft.client.renderer.texture.ITextureObject;
import net.minecraft.client.renderer.texture.TextureManager;
import net.minecraft.client.renderer.tileentity.TileEntitySpecialRenderer;

import net.mcedit.exporter.export.Quad;
import net.minecraft.client.renderer.texture.TextureAtlasSprite;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import net.mcedit.exporter.export.Game;
import net.mcedit.exporter.export.Report;
import net.mcedit.exporter.pack.PackWriter;
import net.minecraft.block.Block;
import net.minecraft.block.properties.IProperty;
import net.minecraft.block.state.IBlockState;
import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.block.model.BakedQuad;
import net.minecraft.client.renderer.block.model.IBakedModel;
import net.minecraft.client.renderer.block.model.ModelResourceLocation;
import net.minecraft.client.renderer.tileentity.TileEntityRendererDispatcher;
import net.minecraft.client.resources.IResource;
import net.minecraft.nbt.NBTTagCompound;
import net.minecraft.tileentity.TileEntity;
import net.minecraft.util.BlockRenderLayer;
import net.minecraft.util.EnumBlockRenderType;
import net.minecraft.util.EnumFacing;
import net.minecraft.util.ResourceLocation;
import net.minecraft.util.math.BlockPos;
import net.minecraftforge.fml.common.Loader;

import java.io.FileNotFoundException;
import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.function.Predicate;
import java.util.stream.Collectors;

/**
 * Reads blocks from Minecraft 1.12.2, in Forge, with MCP's names for the game. The mod loader's part is in Glue.
 *
 * Worlds from this version keep blocks by number, so each block also gives the number it has in this game, and
 * the state the game reads each of its sixteen data values as.
 */
abstract class VersionGame implements Game {
    // MCEdit's numbering, which comes from the render types of this version.
    private static final int RENDER_INVISIBLE = 0;
    private static final int RENDER_LIQUID = 1;
    private static final int RENDER_ENTITY = 2;
    private static final int RENDER_MODEL = 3;

    private static final Map<String, Integer> DATA_VERSIONS = new TreeMap<>();

    static {
        DATA_VERSIONS.put("1.12", 1139);
        DATA_VERSIONS.put("1.12.1", 1241);
        DATA_VERSIONS.put("1.12.2", 1343);
    }

    private final Minecraft minecraft = Minecraft.getMinecraft();
    private Map<IBlockState, ModelResourceLocation> variants;
    private Block variantsBlock;

    @Override
    public String minecraftVersion() {
        return Loader.MC_VERSION;
    }

    @Override
    public int dataVersion() {
        Integer version = DATA_VERSIONS.get(Loader.MC_VERSION);
        return version != null ? version : 0;
    }

    @Override
    public List<String> blockIds() {
        List<String> ids = new ArrayList<>();
        for (ResourceLocation id : Block.REGISTRY.getKeys()) {
            ids.add(id.toString());
        }
        return ids;
    }

    @Override
    public BlockFacts readBlock(String name, Report report) {
        Block block = Block.REGISTRY.getObject(new ResourceLocation(name));
        Map<String, List<String>> properties = new LinkedHashMap<>();
        for (IProperty<?> property : block.getBlockState().getProperties()) {
            properties.put(property.getName(), valueNames(property));
        }
        List<StateFacts> states = new ArrayList<>();
        for (IBlockState state : block.getBlockState().getValidStates()) {
            states.add(readState(name, block, state, report));
        }
        String displayName = report.guarded(name, "displayName", block::getLocalizedName, name);
        String descriptionId = report.guarded(name, "descriptionId", block::getTranslationKey, "");
        BlockFacts facts = new BlockFacts(name, displayName, descriptionId, stateName(block.getDefaultState()),
                properties, states);

        JsonObject legacy = new JsonObject();
        legacy.addProperty("numericId", Block.getIdFromBlock(block));
        JsonObject metaStates = new JsonObject();
        for (int meta = 0; meta < 16; meta++) {
            // A data value the block does not use throws, and is left out. That is not a problem with the export.
            try {
                metaStates.addProperty(String.valueOf(meta), stateName(block.getStateFromMeta(meta)));
            } catch (RuntimeException e) {
                continue;
            }
        }
        legacy.add("metaStates", metaStates);
        facts.legacy = legacy;
        return facts;
    }

    @SuppressWarnings("deprecation")
    private StateFacts readState(String id, Block block, IBlockState state, Report report) {
        Map<String, JsonElement> a = new LinkedHashMap<>();
        a.put("opaqueCube", json(report.guarded(id, "opaqueCube", state::isOpaqueCube, false)));
        a.put("normalCube", json(report.guarded(id, "normalCube", state::isNormalCube, false)));
        a.put("collidable", json(report.guarded(id, "collidable", () -> state.getMaterial().blocksMovement(), true)));
        a.put("opacity", json(report.guarded(id, "opacity", state::getLightOpacity, 15)));
        a.put("brightness", json(report.guarded(id, "brightness", state::getLightValue, 0)));
        a.put("useNeighborBrightness", json(report.guarded(id, "useNeighborBrightness", state::useNeighborBrightness, false)));
        a.put("hasEntity", json(report.guarded(id, "hasEntity", () -> block.hasTileEntity(state), false)));
        a.put("materialLiquid", json(state.getMaterial().isLiquid()));
        a.put("materialMapColor", json(report.guarded(id, "materialMapColor",
                () -> state.getMaterial().getMaterialMapColor().colorValue, 0)));
        a.put("renderLayer", new JsonPrimitive(report.guarded(id, "renderLayer", () -> renderLayer(block), "unknown")));
        a.put("renderType", json(report.guarded(id, "renderType", () -> renderType(state), RENDER_MODEL)));
        a.put("meta", json(report.guarded(id, "meta", () -> block.getMetaFromState(state), 0)));

        IBakedModel model = report.guarded(id, "model",
                () -> minecraft.getBlockRendererDispatcher().getBlockModelShapes().getModelForState(state), null);
        String particle = "";
        if (model != null) {
            particle = report.guarded(id, "particleTexture", () -> model.getParticleTexture().getIconName(), "");
            boolean tinted = report.guarded(id, "tinted", () -> isTinted(model, state), false);
            a.put("tinted", json(tinted));
            if (tinted) {
                a.put("tintColor", json(report.guarded(id, "tintColor",
                        () -> minecraft.getBlockColors().colorMultiplier(state, null, null, 0), -1)));
            }
        }

        ModelResourceLocation location = report.guarded(id, "model location", () -> variantsOf(block).get(state), null);
        String locationName = location != null ? location.getNamespace() + ":" + location.getPath() : id;
        String variant = location != null ? location.getVariant() : "normal";
        StateFacts facts = new StateFacts(stateName(state), a, locationName, variant, particle);
        if (model != null) {
            facts.modelQuads = () -> modelQuads(model, state);
            facts.customModel = Game.isModelFromMod(model);
        }
        if (report.guarded(id, "hasEntity", () -> block.hasTileEntity(state), false)) {
            facts.entityQuads = () -> entityQuads(block, state);
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
            for (BakedQuad quad : model.getQuads(state, side, 0L)) {
                quads.add(quad(quad, side.getName()));
            }
        }
        for (BakedQuad quad : model.getQuads(state, null, 0L)) {
            quads.add(quad(quad, null));
        }
        return quads;
    }

    private static Quad quad(BakedQuad quad, String cullface) {
        TextureAtlasSprite sprite = quad.getSprite();
        return Quad.fromVertexData(quad.getVertexData(), withNamespace(sprite.getIconName()), sprite.getMinU(), sprite.getMaxU(),
                sprite.getMinV(), sprite.getMaxV(), quad.getFace().getName(), cullface, quad.getTintIndex(), true);
    }

    /** "stone" has no namespace before 1.13, and is the game's own. */
    private static String withNamespace(String name) {
        return name.indexOf(':') < 0 ? "minecraft:" + name : name;
    }

    private Map<IBlockState, ModelResourceLocation> variantsOf(Block block) {
        if (block != variantsBlock) {
            variants = minecraft.getBlockRendererDispatcher().getBlockModelShapes().getBlockStateMapper().getVariants(block);
            variantsBlock = block;
        }
        return variants;
    }

    private static boolean isTinted(IBakedModel model, IBlockState state) {
        for (EnumFacing side : EnumFacing.values()) {
            if (anyTinted(model.getQuads(state, side, 0L))) {
                return true;
            }
        }
        return anyTinted(model.getQuads(state, null, 0L));
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
        switch (type) {
            case MODEL:
                return RENDER_MODEL;
            case ENTITYBLOCK_ANIMATED:
                return RENDER_ENTITY;
            case LIQUID:
                return RENDER_LIQUID;
            default:
                return RENDER_INVISIBLE;
        }
    }

    private static String renderLayer(Block block) {
        BlockRenderLayer layer = block.getRenderLayer();
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
        // This version has no list of the blocks a block entity is for, so each block makes one and says.
        Map<String, List<String>> blocksByEntity = new TreeMap<>();
        Map<String, TileEntity> instances = new LinkedHashMap<>();
        for (ResourceLocation blockId : Block.REGISTRY.getKeys()) {
            Block block = Block.REGISTRY.getObject(blockId);
            IBlockState state = block.getDefaultState();
            if (!report.guarded(blockId.toString(), "hasEntity", () -> block.hasTileEntity(state), false)) {
                continue;
            }
            TileEntity instance = report.guarded(blockId.toString(), "a new block entity",
                    () -> block.createTileEntity(null, state), null);
            if (instance == null) {
                continue;
            }
            ResourceLocation entityId = TileEntity.getKey(instance.getClass());
            if (entityId == null || !include.test(entityId.toString())) {
                continue;
            }
            blocksByEntity.computeIfAbsent(entityId.toString(), key -> new ArrayList<>()).add(blockId.toString());
            instances.putIfAbsent(entityId.toString(), instance);
        }
        List<PackWriter.BlockEntity> entities = new ArrayList<>();
        for (Map.Entry<String, List<String>> entry : blocksByEntity.entrySet()) {
            String id = entry.getKey();
            TileEntity instance = instances.get(id);
            Boolean hasRenderer = report.guarded(id, "whether it has a renderer",
                    () -> TileEntityRendererDispatcher.instance.getRenderer(instance) != null, null);
            String defaultNbt = report.guarded(id, "its default NBT", () -> instance.writeToNBT(new NBTTagCompound()).toString(), null);
            List<String> blocks = entry.getValue().stream().sorted().collect(Collectors.toList());
            entities.add(new PackWriter.BlockEntity(id, blocks, hasRenderer, defaultNbt));
        }
        return entities;
    }

    @Override
    public boolean blockstateModelsInBlockFolder() {
        return true;
    }

    /** MCEdit takes the colors of biomes in worlds of this version from what it knows of them itself. */
    @Override
    public JsonObject readBiomes(Predicate<String> include, Report report) {
        return null;
    }

    @Override
    public InputStream openResource(String name) throws IOException {
        try {
            return minecraft.getResourceManager().getResource(new ResourceLocation(name)).getInputStream();
        } catch (FileNotFoundException e) {
            return null;
        }
    }

    @Override
    public boolean isGameResource(String name) {
        try (IResource resource = minecraft.getResourceManager().getResource(new ResourceLocation(name))) {
            return "default".equalsIgnoreCase(resource.getResourcePackName());
        } catch (IOException e) {
            return false;
        }
    }

    /** The canonical name of a state: properties sorted by name, or empty if it has none. */
    private static String stateName(IBlockState state) {
        List<IProperty<?>> properties = new ArrayList<>(state.getPropertyKeys());
        properties.sort(Comparator.comparing(IProperty::getName));
        StringBuilder name = new StringBuilder();
        for (IProperty<?> property : properties) {
            if (name.length() > 0) {
                name.append(',');
            }
            name.append(property.getName()).append('=').append(valueName(state, property));
        }
        return name.toString();
    }

    private static <T extends Comparable<T>> String valueName(IBlockState state, IProperty<T> property) {
        return property.getName(state.getValue(property));
    }

    private static <T extends Comparable<T>> List<String> valueNames(IProperty<T> property) {
        Collection<T> values = property.getAllowedValues();
        List<String> names = new ArrayList<>();
        for (T value : values) {
            names.add(property.getName(value));
        }
        return names;
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
        TileEntity entity = block.createTileEntity(null, state);
        if (entity == null) {
            return null;
        }
        TileEntitySpecialRenderer<TileEntity> renderer = TileEntityRendererDispatcher.instance.getRenderer(entity);
        if (renderer == null) {
            return null;
        }
        // The block and state the renderer asks the block entity for, which would come from a world.
        FeedbackCapture.setFieldOfType(entity, TileEntity.class, Block.class, block);
        FeedbackCapture.setFieldOfType(entity, TileEntity.class, int.class, block.getMetaFromState(state));
        Runnable draw = () -> {
            try {
                renderer.render(entity, 0, 0, 0, 0, -1, 1f);
            } catch (RuntimeException e) {
                finishDrawing();
                throw e;
            }
        };
        // In a world of its one block first, as many renderers look there for what to draw; then in none, as some
        // ask a world more than that one can answer.
        RuntimeException inWorld = null;
        OneBlockWorld world = FeedbackCapture.allocate(OneBlockWorld.class);
        if (world != null) {
            world.state = state;
            world.entity = entity;
            // The overworld's provider, for those that ask whether there is sky light.
            FeedbackCapture.setFieldOfType(world, net.minecraft.world.World.class, net.minecraft.world.WorldProvider.class,
                    new net.minecraft.world.WorldProviderSurface());
            entity.setWorld(world);
            entity.setPos(BlockPos.ORIGIN);
            // Some renderers look at the game's world rather than their block entity's: it is this one while they
            // draw, as before a world is joined there is none.
            boolean gameWorld = minecraft.world == null;
            if (gameWorld) {
                minecraft.world = world;
            }
            try {
                return drawnByRenderer(draw);
            } catch (RuntimeException e) {
                inWorld = e;
                entity.setWorld(null);
            } finally {
                if (gameWorld) {
                    minecraft.world = null;
                }
            }
        }
        try {
            return drawnByRenderer(draw);
        } catch (RuntimeException e) {
            // What went wrong in its world says more than what went wrong in none.
            throw inWorld != null ? inWorld : e;
        }
    }

    /**
     * A world with one block in it, at the origin, made without its constructor: renderers ask it for the block and
     * block entity there, and its light and time, which it answers itself.
     */
    private static final class OneBlockWorld extends net.minecraft.client.multiplayer.WorldClient {
        IBlockState state;
        TileEntity entity;

        private OneBlockWorld() {
            super(null, null, 0, null, null);
        }

        /** A chunk that answers with this world's block, for renderers that read a ChunkCache of the world. */
        @Override
        public net.minecraft.world.chunk.Chunk getChunk(int x, int z) {
            OneBlockWorld world = this;
            return new net.minecraft.world.chunk.Chunk(this, x, z) {
                @Override
                public IBlockState getBlockState(BlockPos pos) {
                    return world.getBlockState(pos);
                }

                @Override
                public IBlockState getBlockState(int x, int y, int z) {
                    return world.getBlockState(new BlockPos(x, y, z));
                }

                @Override
                public TileEntity getTileEntity(BlockPos pos, net.minecraft.world.chunk.Chunk.EnumCreateEntityType type) {
                    return world.getTileEntity(pos);
                }

                @Override
                public int getLightFor(net.minecraft.world.EnumSkyBlock type, BlockPos pos) {
                    return 15;  // full light, as the world it would ask is not there
                }

                @Override
                public int getLightSubtracted(BlockPos pos, int amount) {
                    return 15;
                }
            };
        }

        @Override
        protected net.minecraft.world.chunk.IChunkProvider createChunkProvider() {
            return null;
        }

        @Override
        protected boolean isChunkLoaded(int x, int z, boolean allowEmpty) {
            return false;
        }

        @Override
        public IBlockState getBlockState(BlockPos pos) {
            return BlockPos.ORIGIN.equals(pos) ? state : net.minecraft.init.Blocks.AIR.getDefaultState();
        }

        @Override
        public TileEntity getTileEntity(BlockPos pos) {
            return BlockPos.ORIGIN.equals(pos) ? entity : null;
        }

        @Override
        public boolean isAirBlock(BlockPos pos) {
            return !BlockPos.ORIGIN.equals(pos);
        }

        @Override
        public int getCombinedLight(BlockPos pos, int lightValue) {
            return 0xF000F0;
        }

        @Override
        public long getTotalWorldTime() {
            return 0;
        }

        @Override
        public long getWorldTime() {
            return 6000;
        }
    }

    private List<FeedbackCapture.Sprite> feedbackSprites;

    private List<Quad> drawnByRenderer(Runnable draw) {
        if (feedbackSprites == null) {
            feedbackSprites = new ArrayList<>();
            for (Object value : FeedbackCapture.mapValues(minecraft.getTextureMapBlocks(), TextureAtlasSprite.class)) {
                TextureAtlasSprite sprite = (TextureAtlasSprite) value;
                feedbackSprites.add(new FeedbackCapture.Sprite(sprite.getIconName(), sprite.getMinU(), sprite.getMaxU(),
                        sprite.getMinV(), sprite.getMaxV()));
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
            super(Minecraft.getMinecraft().getResourceManager());
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
