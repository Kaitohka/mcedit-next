package @PACKAGE@;

import net.mcedit.exporter.export.Quad;
import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.HashMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import com.mojang.blaze3d.vertex.PoseStack;
import com.mojang.blaze3d.vertex.VertexConsumer;
import com.mojang.blaze3d.vertex.VertexFormat;
import net.minecraft.client.renderer.MultiBufferSource;
import net.minecraft.client.renderer.blockentity.BlockEntityRenderer;
import net.minecraft.client.renderer.texture.OverlayTexture;
import net.minecraft.client.renderer.texture.TextureAtlas;
import net.minecraft.client.renderer.texture.TextureAtlasSprite;
import net.minecraft.world.level.block.EntityBlock;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import com.mojang.serialization.JsonOps;
import net.mcedit.exporter.export.Game;
import net.mcedit.exporter.export.Report;
import net.mcedit.exporter.pack.PackWriter;
import net.minecraft.SharedConstants;
import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.ItemBlockRenderTypes;
import net.minecraft.client.renderer.RenderType;
import net.minecraft.client.renderer.block.BlockModelShaper;
import net.minecraft.client.renderer.block.model.BakedQuad;
import net.minecraft.client.resources.model.BakedModel;
import net.minecraft.client.resources.model.ModelResourceLocation;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.Registry;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.RegistryOps;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.packs.resources.Resource;
import net.minecraft.util.RandomSource;
import net.minecraft.world.level.BlockGetter;
import net.minecraft.world.level.EmptyBlockGetter;
import net.minecraft.world.level.biome.Biome;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.RenderShape;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.block.entity.BlockEntityType;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.Property;

import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.function.Predicate;
import java.util.stream.Collectors;

/** Reads blocks from Minecraft 1.20.1. The mod loader's part is in Glue. */
abstract class VersionGame implements Game {
    // MCEdit's numbering, which comes from the render types of Minecraft 1.12.
    private static final int RENDER_INVISIBLE = 0;
    private static final int RENDER_LIQUID = 1;
    private static final int RENDER_ENTITY = 2;
    private static final int RENDER_MODEL = 3;

    private static final BlockGetter NO_WORLD = EmptyBlockGetter.INSTANCE;
    private static final BlockPos ORIGIN = BlockPos.ZERO;
    private static final String VANILLA_PACK_ID = "vanilla";

    private final Minecraft minecraft = Minecraft.getInstance();
    private final RandomSource random = RandomSource.create(42);

    @Override
    public String minecraftVersion() {
        return SharedConstants.getCurrentVersion().getName();
    }

    @Override
    public int dataVersion() {
        return SharedConstants.getCurrentVersion().getDataVersion().getVersion();
    }

    @Override
    public List<String> blockIds() {
        List<String> ids = new ArrayList<>();
        for (ResourceLocation id : BuiltInRegistries.BLOCK.keySet()) {
            ids.add(id.toString());
        }
        return ids;
    }

    @Override
    public BlockFacts readBlock(String name, Report report) {
        Block block = BuiltInRegistries.BLOCK.get(new ResourceLocation(name));
        Map<String, List<String>> properties = new LinkedHashMap<>();
        for (Property<?> property : block.getStateDefinition().getProperties()) {
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
        a.put("hasEntity", json(state.hasBlockEntity()));
        a.put("materialLiquid", json(state.liquid()));
        a.put("materialMapColor", json(report.guarded(id, "materialMapColor", () -> state.getMapColor(NO_WORLD, ORIGIN).col, 0)));
        a.put("renderLayer", new JsonPrimitive(report.guarded(id, "renderLayer", () -> renderLayer(state), "unknown")));
        a.put("renderType", json(renderType(state)));
        a.put("cullsSameBlock", json(report.guarded(id, "cullsSameBlock", () -> state.skipRendering(state, Direction.NORTH), false)));

        BakedModel model = report.guarded(id, "model", () -> minecraft.getBlockRenderer().getBlockModel(state), null);
        String particle = "";
        if (model != null) {
            particle = report.guarded(id, "particleTexture", () -> model.getParticleIcon().contents().name().toString(), "");
            boolean tinted = report.guarded(id, "tinted", () -> isTinted(model, state), false);
            a.put("tinted", json(tinted));
            if (tinted) {
                // Without a world, a biome-dependent color provider returns its default color.
                a.put("tintColor", json(report.guarded(id, "tintColor",
                        () -> minecraft.getBlockColors().getColor(state, null, null, 0), -1)));
            }
        }

        ModelResourceLocation location = BlockModelShaper.stateToModelLocation(state);
        StateFacts facts = new StateFacts(stateName(state), a, location.getNamespace() + ":" + location.getPath(),
                location.getVariant(), particle);
        if (model != null) {
            facts.modelQuads = () -> modelQuads(model, state);
            facts.customModel = Game.isModelFromMod(model);
        }
        if (state.hasBlockEntity()) {
            facts.entityQuads = () -> entityQuads(state);
        }
        return facts;
    }

    /**
     * The faces of a state's model, as the game draws them: those hidden by each side's neighbor, then the rest. A
     * model whose block a block entity renderer draws in the world may give its faces only for the item, with no
     * state, as HBM's machines do; then those are taken.
     */
    private List<Quad> modelQuads(BakedModel model, BlockState state) {
        List<Quad> quads = modelQuadsOf(model, state);
        return quads.isEmpty() && state != null ? modelQuadsOf(model, null) : quads;
    }

    private List<Quad> modelQuadsOf(BakedModel model, BlockState state) {
        List<Quad> quads = new ArrayList<>();
        for (Direction direction : Direction.values()) {
            for (BakedQuad quad : model.getQuads(state, direction, RandomSource.create(42))) {
                quads.add(quad(quad, direction.getName()));
            }
        }
        for (BakedQuad quad : model.getQuads(state, null, RandomSource.create(42))) {
            quads.add(quad(quad, null));
        }
        return quads;
    }

    private static Quad quad(BakedQuad quad, String cullface) {
        TextureAtlasSprite sprite = quad.getSprite();
        return Quad.fromVertexData(quad.getVertices(), sprite.contents().name().toString(), sprite.getU0(),
                sprite.getU1(), sprite.getV0(), sprite.getV1(), quad.getDirection().getName(), cullface,
                quad.getTintIndex(), quad.isShade());
    }

    /**
     * What the block entity renderer of a state draws, with a block entity made for it and nothing else. In a
     * world, the block entity is in it, as some renderers look at the world.
     */
    private List<Quad> entityQuads(BlockState state) {
        if (!(state.getBlock() instanceof EntityBlock)) {
            return null;
        }
        BlockEntity entity = ((EntityBlock) state.getBlock()).newBlockEntity(BlockPos.ZERO, state);
        if (entity == null) {
            return null;
        }
        if (minecraft.level != null) {
            entity.setLevel(minecraft.level);
        }
        BlockEntityRenderer<BlockEntity> renderer = minecraft.getBlockEntityRenderDispatcher().getRenderer(entity);
        if (renderer == null) {
            return null;
        }
        Capture capture = new Capture();
        renderer.render(entity, 0, new PoseStack(), capture, 0xF000F0, OverlayTexture.NO_OVERLAY);
        return capture.finish();
    }

    private static final Pattern RENDER_TYPE_TEXTURE = Pattern.compile(
            "(?:texture\\[Optional\\[|TextureBinding\\[location=)([a-z0-9_.-]+:[^\\],\\s(]+)");

    /** The texture a render type draws with, from its description, which is the same in every mod loader. */
    private static String textureOf(Object type) {
        Matcher matcher = RENDER_TYPE_TEXTURE.matcher(type.toString());
        return matcher.find() ? matcher.group(1) : null;
    }

    private final Map<String, List<TextureAtlasSprite>> atlasSprites = new HashMap<>();

    /** The sprites of an atlas, which it keeps in a private map, found by its type as names differ between loaders. */
    private List<TextureAtlasSprite> spritesOf(String atlas) {
        return atlasSprites.computeIfAbsent(atlas, name -> {
            List<TextureAtlasSprite> sprites = new ArrayList<>();
            Object texture = minecraft.getTextureManager().getTexture(new ResourceLocation(name));
            if (texture instanceof TextureAtlas) {
                for (Field field : TextureAtlas.class.getDeclaredFields()) {
                    if (!Map.class.isAssignableFrom(field.getType()) || Modifier.isStatic(field.getModifiers())) {
                        continue;
                    }
                    try {
                        field.setAccessible(true);
                        for (Object value : ((Map<?, ?>) field.get(texture)).values()) {
                            if (value instanceof TextureAtlasSprite) {
                                sprites.add((TextureAtlasSprite) value);
                            }
                        }
                    } catch (ReflectiveOperationException | RuntimeException ignored) {
                        // not the map of sprites
                    }
                }
            }
            return sprites;
        });
    }

    /**
     * A face drawn with a texture: on an atlas, the sprite its middle is in, with the texture coordinates made
     * relative to it; any other texture is a file of its own, as "namespace:textures/entity/thing.png".
     */
    private Quad capturedQuad(String texture, float[] corners, float nx, float ny, float nz, int color) {
        if (texture.contains(":textures/atlas/")) {
            float u = (corners[3] + corners[8] + corners[13] + corners[18]) / 4;
            float v = (corners[4] + corners[9] + corners[14] + corners[19]) / 4;
            // The smallest sprite there: one whose file was missing can have bounds over the whole atlas.
            TextureAtlasSprite found = null;
            float area = Float.MAX_VALUE;
            for (TextureAtlasSprite sprite : spritesOf(texture)) {
                float size = (sprite.getU1() - sprite.getU0()) * (sprite.getV1() - sprite.getV0());
                if (u >= sprite.getU0() && u <= sprite.getU1() && v >= sprite.getV0() && v <= sprite.getV1()
                        && size < area) {
                    found = sprite;
                    area = size;
                }
            }
            if (found == null) {
                return null;
            }
            for (int i = 0; i < 4; i++) {
                corners[i * 5 + 3] = (corners[i * 5 + 3] - found.getU0()) / (found.getU1() - found.getU0());
                corners[i * 5 + 4] = (corners[i * 5 + 4] - found.getV0()) / (found.getV1() - found.getV0());
            }
            texture = found.contents().name().toString();
        }
        String side = nx == 0 && ny == 0 && nz == 0 ? Quad.nearestSide(corners) : Quad.nearestSide(nx, ny, nz);
        return new Quad(corners, texture, side, null, -1, true, color);
    }

    /** Buffers that keep the faces drawn into them. */
    private final class Capture implements MultiBufferSource {
        private final List<Quad> quads = new ArrayList<>();
        private final List<Recorder> recorders = new ArrayList<>();

        @Override
        public VertexConsumer getBuffer(RenderType type) {
            Recorder recorder = new Recorder(type.mode() == VertexFormat.Mode.QUADS ? textureOf(type) : null, quads);
            recorders.add(recorder);
            return recorder;
        }

        List<Quad> finish() {
            for (Recorder recorder : recorders) {
                recorder.flush();
            }
            return quads;
        }
    }

    /** Takes the vertices of faces of four corners, and keeps them as faces. */
    private final class Recorder implements VertexConsumer {
        private final String texture;
        private final List<Quad> quads;
        private float[] corners = new float[20];
        private int vertex = -1;
        private int color = -1;
        private float nx, ny, nz;

        Recorder(String texture, List<Quad> quads) {
            this.texture = texture;
            this.quads = quads;
        }

        void flush() {
            if (vertex == 3 && texture != null) {
                Quad quad = capturedQuad(texture, corners, nx, ny, nz, color == 0xffffffff ? -1 : color);
                if (quad != null) {
                    quads.add(quad);
                }
            }
            corners = new float[20];
            vertex = -1;
        }

        private void begin(float x, float y, float z) {
            if (vertex == 3) {
                flush();
            }
            vertex++;
            corners[vertex * 5] = x;
            corners[vertex * 5 + 1] = y;
            corners[vertex * 5 + 2] = z;
        }

        private void color(int r, int g, int b, int a, boolean unused) {
            if (vertex == 0) {
                color = (a & 0xff) << 24 | (r & 0xff) << 16 | (g & 0xff) << 8 | b & 0xff;
            }
        }

        private void texture(float u, float v) {
            if (vertex >= 0) {
                corners[vertex * 5 + 3] = u;
                corners[vertex * 5 + 4] = v;
            }
        }

        private void normalOf(float x, float y, float z) {
            if (vertex == 0) {
                nx = x;
                ny = y;
                nz = z;
            }
        }

        @Override
        public VertexConsumer vertex(double x, double y, double z) {
            begin((float) x, (float) y, (float) z);
            return this;
        }

        @Override
        public VertexConsumer color(int r, int g, int b, int a) {
            color(r, g, b, a, true);
            return this;
        }

        @Override
        public VertexConsumer uv(float u, float v) {
            texture(u, v);
            return this;
        }

        @Override
        public VertexConsumer overlayCoords(int u, int v) {
            return this;
        }

        @Override
        public VertexConsumer uv2(int u, int v) {
            return this;
        }

        @Override
        public VertexConsumer normal(float x, float y, float z) {
            normalOf(x, y, z);
            return this;
        }

        @Override
        public void endVertex() {
        }

        public void defaultColor(int r, int g, int b, int a) {
        }

        public void unsetDefaultColor() {
        }
    }

    private boolean isTinted(BakedModel model, BlockState state) {
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

    @SuppressWarnings("deprecation")
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

    @Override
    public List<PackWriter.BlockEntity> readBlockEntities(Predicate<String> include, Report report) {
        List<Block> blocksWithEntities = BuiltInRegistries.BLOCK.stream()
                .filter(block -> block.defaultBlockState().hasBlockEntity())
                .collect(Collectors.toList());
        List<PackWriter.BlockEntity> entities = new ArrayList<>();
        for (BlockEntityType<?> type : BuiltInRegistries.BLOCK_ENTITY_TYPE) {
            ResourceLocation key = BuiltInRegistries.BLOCK_ENTITY_TYPE.getKey(type);
            if (key == null || !include.test(key.toString())) {
                continue;
            }
            String id = key.toString();
            List<Block> valid = blocksWithEntities.stream()
                    .filter(block -> type.isValid(block.defaultBlockState()))
                    .collect(Collectors.toList());
            List<String> blocks = valid.stream()
                    .map(block -> BuiltInRegistries.BLOCK.getKey(block).toString())
                    .sorted()
                    .collect(Collectors.toList());
            Boolean hasRenderer = null;
            String defaultNbt = null;
            if (!valid.isEmpty()) {
                BlockEntity instance = report.guarded(id, "a new block entity",
                        () -> type.create(BlockPos.ZERO, valid.get(0).defaultBlockState()), null);
                if (instance != null) {
                    hasRenderer = report.guarded(id, "whether it has a renderer",
                            () -> minecraft.getBlockEntityRenderDispatcher().getRenderer(instance) != null, null);
                    defaultNbt = report.guarded(id, "its default NBT", () -> instance.saveWithFullMetadata().toString(), null);
                }
            }
            entities.add(new PackWriter.BlockEntity(id, blocks, hasRenderer, defaultNbt));
        }
        return entities;
    }

    /** Biomes come from data packs, so they are only known while in a world. */
    @Override
    public JsonObject readBiomes(Predicate<String> include, Report report) {
        if (minecraft.level == null) {
            return null;
        }
        Registry<Biome> registry = minecraft.level.registryAccess().registryOrThrow(Registries.BIOME);
        RegistryOps<JsonElement> ops = RegistryOps.create(JsonOps.INSTANCE, minecraft.level.registryAccess());
        JsonObject biomes = new JsonObject();
        for (Map.Entry<net.minecraft.resources.ResourceKey<Biome>, Biome> entry : registry.entrySet()) {
            String id = entry.getKey().location().toString();
            if (!include.test(id)) {
                continue;
            }
            JsonElement json = report.guarded(id, "the biome", () -> Biome.DIRECT_CODEC.encodeStart(ops, entry.getValue())
                    .result().orElse(null), null);
            if (json != null && json.isJsonObject()) {
                biomes.add(id, climateOnly(json.getAsJsonObject()));
            }
        }
        return biomes;
    }

    /** The parts of a biome's description MCEdit uses: its climate and colors. */
    private static JsonObject climateOnly(JsonObject biome) {
        JsonObject json = new JsonObject();
        for (String key : new String[]{"temperature", "downfall", "effects"}) {
            if (biome.has(key)) {
                json.add(key, biome.get(key));
            }
        }
        return json;
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
        Optional<Resource> resource = minecraft.getResourceManager().getResource(new ResourceLocation(name));
        return resource.isPresent() ? resource.get().open() : null;
    }

    @Override
    public boolean isGameResource(String name) {
        Optional<Resource> resource = minecraft.getResourceManager().getResource(new ResourceLocation(name));
        return resource.isPresent() && resource.get().sourcePackId().equals(VANILLA_PACK_ID);
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

    private static JsonElement json(boolean value) {
        return new JsonPrimitive(value);
    }

    private static JsonElement json(int value) {
        return new JsonPrimitive(value);
    }
}
