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
import net.minecraft.client.resources.model.IBakedModel;
import net.minecraft.client.resources.model.ModelResourceLocation;
import net.minecraft.client.renderer.tileentity.TileEntityRendererDispatcher;
import net.minecraft.client.resources.IResource;
import net.minecraft.nbt.NBTTagCompound;
import net.minecraft.tileentity.TileEntity;
import net.minecraft.util.EnumFacing;
import net.minecraft.util.EnumWorldBlockLayer;
import net.minecraft.util.ResourceLocation;
import net.minecraft.util.BlockPos;
import net.minecraftforge.fml.common.Loader;

import java.io.FileNotFoundException;
import java.io.IOException;
import java.io.InputStream;
import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.function.Predicate;
import java.util.stream.Collectors;

/**
 * Reads blocks from Minecraft 1.8.9, in Forge, with MCP's names for the game. The mod loader's part is in Glue.
 *
 * In this version most of what a block is like is the same for all of its states, so it is asked of the block.
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

    private final Minecraft minecraft = Minecraft.getMinecraft();
    private Map<IBlockState, ModelResourceLocation> variants;

    @Override
    public String minecraftVersion() {
        return Loader.MC_VERSION;
    }

    /** Data versions began with 1.9. */
    @Override
    public int dataVersion() {
        return 0;
    }

    @Override
    public List<String> blockIds() {
        List<String> ids = new ArrayList<>();
        for (ResourceLocation id : Block.blockRegistry.getKeys()) {
            ids.add(id.toString());
        }
        return ids;
    }

    @Override
    public BlockFacts readBlock(String name, Report report) {
        Block block = Block.blockRegistry.getObject(new ResourceLocation(name));
        Map<String, List<String>> properties = new LinkedHashMap<>();
        for (IProperty<?> property : block.getBlockState().getProperties()) {
            properties.put(property.getName(), valueNames(property));
        }
        List<StateFacts> states = new ArrayList<>();
        for (IBlockState state : block.getBlockState().getValidStates()) {
            states.add(readState(name, block, state, report));
        }
        String displayName = report.guarded(name, "displayName", block::getLocalizedName, name);
        String descriptionId = report.guarded(name, "descriptionId", block::getUnlocalizedName, "");
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
        a.put("opaqueCube", json(report.guarded(id, "opaqueCube", block::isOpaqueCube, false)));
        a.put("normalCube", json(report.guarded(id, "normalCube", block::isNormalCube, false)));
        a.put("collidable", json(report.guarded(id, "collidable", () -> block.getMaterial().blocksMovement(), true)));
        a.put("opacity", json(report.guarded(id, "opacity", block::getLightOpacity, 15)));
        a.put("brightness", json(report.guarded(id, "brightness", block::getLightValue, 0)));
        a.put("useNeighborBrightness", json(report.guarded(id, "useNeighborBrightness", block::getUseNeighborBrightness, false)));
        a.put("hasEntity", json(report.guarded(id, "hasEntity", () -> block.hasTileEntity(state), false)));
        a.put("materialLiquid", json(block.getMaterial().isLiquid()));
        a.put("materialMapColor", json(report.guarded(id, "materialMapColor",
                () -> block.getMaterial().getMaterialMapColor().colorValue, 0)));
        a.put("renderLayer", new JsonPrimitive(report.guarded(id, "renderLayer", () -> renderLayer(block), "unknown")));
        a.put("renderType", json(report.guarded(id, "renderType", () -> renderType(block), RENDER_MODEL)));
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
                        () -> block.getRenderColor(state), -1)));
            }
        }

        ModelResourceLocation location = report.guarded(id, "model location", () -> variants().get(state), null);
        String locationName = location != null ? location.getResourceDomain() + ":" + location.getResourcePath() : id;
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
    private List<Quad> modelQuads(IBakedModel model, IBlockState state) {
        List<Quad> quads = modelQuadsOf(model, state);
        return quads.isEmpty() && state != null ? modelQuadsOf(model, null) : quads;
    }

    private List<Quad> modelQuadsOf(IBakedModel model, IBlockState state) {
        List<Quad> quads = new ArrayList<>();
        for (EnumFacing side : EnumFacing.values()) {
            for (BakedQuad quad : model.getFaceQuads(side)) {
                quads.add(quad(quad, side.getName()));
            }
        }
        for (BakedQuad quad : model.getGeneralQuads()) {
            quads.add(quad(quad, null));
        }
        return quads;
    }

    private Quad quad(BakedQuad quad, String cullface) {
        int[] data = quad.getVertexData();
        int stride = data.length / 4;
        float u = 0;
        float v = 0;
        for (int i = 0; i < 4; i++) {
            u += Float.intBitsToFloat(data[i * stride + 4]) / 4;
            v += Float.intBitsToFloat(data[i * stride + 5]) / 4;
        }
        // The smallest sprite there: one whose file was missing can have bounds over the whole atlas.
        TextureAtlasSprite sprite = null;
        float area = Float.MAX_VALUE;
        for (TextureAtlasSprite candidate : blockSprites()) {
            float size = (candidate.getMaxU() - candidate.getMinU()) * (candidate.getMaxV() - candidate.getMinV());
            if (u >= candidate.getMinU() && u <= candidate.getMaxU() && v >= candidate.getMinV() && v <= candidate.getMaxV()
                    && size < area) {
                sprite = candidate;
                area = size;
            }
        }
        if (sprite == null) {
            sprite = minecraft.getTextureMapBlocks().getMissingSprite();
        }
        return Quad.fromVertexData(quad.getVertexData(), withNamespace(sprite.getIconName()), sprite.getMinU(), sprite.getMaxU(),
                sprite.getMinV(), sprite.getMaxV(), quad.getFace().getName(), cullface, quad.getTintIndex(), true);
    }

    /** "stone" has no namespace before 1.13, and is the game's own. */
    private static String withNamespace(String name) {
        return name.indexOf(':') < 0 ? "minecraft:" + name : name;
    }

    private List<TextureAtlasSprite> blockSprites;

    /** The sprites of the block atlas, which it keeps in a private map, found by its type. */
    private List<TextureAtlasSprite> blockSprites() {
        if (blockSprites == null) {
            blockSprites = new ArrayList<>();
            for (java.lang.reflect.Field field : net.minecraft.client.renderer.texture.TextureMap.class.getDeclaredFields()) {
                if (!Map.class.isAssignableFrom(field.getType()) || java.lang.reflect.Modifier.isStatic(field.getModifiers())) {
                    continue;
                }
                try {
                    field.setAccessible(true);
                    for (Object value : ((Map<?, ?>) field.get(minecraft.getTextureMapBlocks())).values()) {
                        if (value instanceof TextureAtlasSprite && !blockSprites.contains(value)) {
                            blockSprites.add((TextureAtlasSprite) value);
                        }
                    }
                } catch (ReflectiveOperationException | RuntimeException ignored) {
                    // not a map of sprites
                }
            }
        }
        return blockSprites;
    }

    /** This version only gives the model of every state of every block at once, so that is done once. */
    private Map<IBlockState, ModelResourceLocation> variants() {
        if (variants == null) {
            variants = minecraft.getBlockRendererDispatcher().getBlockModelShapes().getBlockStateMapper()
                    .putAllStateModelLocations();
        }
        return variants;
    }

    private static boolean isTinted(IBakedModel model, IBlockState state) {
        for (EnumFacing side : EnumFacing.values()) {
            if (anyTinted(model.getFaceQuads(side))) {
                return true;
            }
        }
        return anyTinted(model.getGeneralQuads());
    }

    private static boolean anyTinted(List<BakedQuad> quads) {
        for (BakedQuad quad : quads) {
            if (quad.hasTintIndex()) {
                return true;
            }
        }
        return false;
    }

    /** The render types of this version are numbers, and MCEdit's are the same ones. */
    private static int renderType(Block block) {
        int type = block.getRenderType();
        return type == RENDER_MODEL || type == RENDER_ENTITY || type == RENDER_LIQUID ? type : RENDER_INVISIBLE;
    }

    private static String renderLayer(Block block) {
        EnumWorldBlockLayer layer = block.getBlockLayer();
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
        Map<Class<?>, String> namesByClass = tileEntityNames();
        for (ResourceLocation blockId : Block.blockRegistry.getKeys()) {
            Block block = Block.blockRegistry.getObject(blockId);
            IBlockState state = block.getDefaultState();
            if (!report.guarded(blockId.toString(), "hasEntity", () -> block.hasTileEntity(state), false)) {
                continue;
            }
            TileEntity instance = report.guarded(blockId.toString(), "a new block entity",
                    () -> block.createTileEntity(null, state), null);
            if (instance == null) {
                continue;
            }
            String entityId = withNamespace(namesByClass.get(instance.getClass()), blockId.toString());
            if (entityId == null || !include.test(entityId)) {
                continue;
            }
            blocksByEntity.computeIfAbsent(entityId, key -> new ArrayList<>()).add(blockId.toString());
            instances.putIfAbsent(entityId, instance);
        }
        List<PackWriter.BlockEntity> entities = new ArrayList<>();
        for (Map.Entry<String, List<String>> entry : blocksByEntity.entrySet()) {
            String id = entry.getKey();
            TileEntity instance = instances.get(id);
            Boolean hasRenderer = report.guarded(id, "whether it has a renderer",
                    () -> TileEntityRendererDispatcher.instance.getSpecialRenderer(instance) != null, null);
            String defaultNbt = report.guarded(id, "its default NBT", () -> {
                NBTTagCompound tag = new NBTTagCompound();
                instance.writeToNBT(tag);
                return tag.toString();
            }, null);
            List<String> blocks = entry.getValue().stream().sorted().collect(Collectors.toList());
            entities.add(new PackWriter.BlockEntity(id, blocks, hasRenderer, defaultNbt));
        }
        return entities;
    }

    /**
     * The names block entities are saved by, which TileEntity keeps in a private map. Before 1.11 they have no
     * namespace.
     */
    /** A block entity's name, with the namespace of the block it was made for if it has none of its own. */
    private static String withNamespace(String name, String blockId) {
        if (name == null || name.indexOf(':') >= 0) {
            return name;
        }
        int colon = blockId.indexOf(':');
        return (colon < 0 ? "minecraft" : blockId.substring(0, colon)) + ":" + name;
    }

    @SuppressWarnings("unchecked")
    private static Map<Class<?>, String> tileEntityNames() {
        for (Field field : TileEntity.class.getDeclaredFields()) {
            if (Map.class.isAssignableFrom(field.getType()) && java.lang.reflect.Modifier.isStatic(field.getModifiers())) {
                try {
                    field.setAccessible(true);
                    Map<Object, Object> map = (Map<Object, Object>) field.get(null);
                    Map<Class<?>, String> names = new HashMap<>();
                    for (Map.Entry<Object, Object> entry : map.entrySet()) {
                        if (entry.getKey() instanceof Class && entry.getValue() instanceof String) {
                            names.put((Class<?>) entry.getKey(), (String) entry.getValue());
                        }
                    }
                    if (!names.isEmpty()) {
                        return names;
                    }
                } catch (ReflectiveOperationException | RuntimeException e) {
                    continue;
                }
            }
        }
        return new HashMap<>();
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
        try {
            IResource resource = minecraft.getResourceManager().getResource(new ResourceLocation(name));
            return "default".equalsIgnoreCase(resource.getResourcePackName());
        } catch (IOException e) {
            return false;
        }
    }

    /** The canonical name of a state: properties sorted by name, or empty if it has none. */
    private static String stateName(IBlockState state) {
        List<IProperty<?>> properties = new ArrayList<>();
        for (Object property : state.getPropertyNames()) {
            properties.add((IProperty<?>) property);
        }
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
        TileEntitySpecialRenderer<TileEntity> renderer = TileEntityRendererDispatcher.instance.getSpecialRenderer(entity);
        if (renderer == null) {
            return null;
        }
        // The block and state the renderer asks the block entity for, which would come from a world.
        FeedbackCapture.setFieldOfType(entity, TileEntity.class, Block.class, block);
        FeedbackCapture.setFieldOfType(entity, TileEntity.class, int.class, block.getMetaFromState(state));
        Runnable draw = () -> {
            try {
                renderer.renderTileEntityAt(entity, 0, 0, 0, 0, -1);
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
            entity.setWorldObj(world);
            entity.setPos(BlockPos.ORIGIN);
            // Some renderers look at the game's world rather than their block entity's: it is this one while they
            // draw, as before a world is joined there is none.
            boolean gameWorld = minecraft.theWorld == null;
            if (gameWorld) {
                minecraft.theWorld = world;
            }
            try {
                return drawnByRenderer(draw);
            } catch (RuntimeException e) {
                inWorld = e;
                entity.setWorldObj(null);
            } finally {
                if (gameWorld) {
                    minecraft.theWorld = null;
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
        public net.minecraft.world.chunk.Chunk getChunkFromChunkCoords(int x, int z) {
            OneBlockWorld world = this;
            return new net.minecraft.world.chunk.Chunk(this, x, z) {
                @Override
                public IBlockState getBlockState(BlockPos pos) {
                    return world.getBlockState(pos);
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
            return BlockPos.ORIGIN.equals(pos) ? state : net.minecraft.init.Blocks.air.getDefaultState();
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

        @Override
        protected int getRenderDistanceChunks() {
            return 1;
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
