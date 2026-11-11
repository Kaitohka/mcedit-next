package @PACKAGE@;

import com.mojang.blaze3d.vertex.PoseStack;
import com.mojang.blaze3d.vertex.VertexConsumer;
import net.minecraft.client.model.Model;
import net.minecraft.client.model.geom.ModelPart;
import net.minecraft.client.renderer.SubmitNodeCollector;
import net.minecraft.client.renderer.blockentity.BlockEntityRenderer;
import net.minecraft.client.renderer.blockentity.state.BlockEntityRenderState;
import net.minecraft.client.renderer.rendertype.RenderType;
import net.minecraft.client.renderer.texture.OverlayTexture;
import net.minecraft.client.renderer.texture.TextureAtlas;
import net.minecraft.world.level.block.EntityBlock;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.lang.reflect.Proxy;
import java.util.HashMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import net.mcedit.exporter.export.Quad;
import net.minecraft.client.renderer.texture.TextureAtlasSprite;
import net.minecraft.client.model.geom.builders.UVPair;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import com.mojang.serialization.JsonOps;
import net.mcedit.exporter.export.Game;
import net.mcedit.exporter.export.Report;
import net.mcedit.exporter.pack.PackWriter;
import net.minecraft.SharedConstants;
import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.chunk.ChunkSectionLayer;
import net.minecraft.client.color.block.BlockTintSource;
import net.minecraft.client.renderer.block.dispatch.BlockStateModel;
import net.minecraft.client.renderer.block.dispatch.BlockStateModelPart;
import net.minecraft.client.resources.model.geometry.BakedQuad;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.Registry;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.RegistryOps;
import net.minecraft.resources.Identifier;
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
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.function.Predicate;
import java.util.stream.Collectors;

/** Reads blocks from Minecraft 26.2. The mod loader's part is in Glue. */
abstract class VersionGame implements Game {
    // MCEdit's numbering, which comes from the render types of Minecraft 1.12.
    private static final int RENDER_INVISIBLE = 0;
    private static final int RENDER_LIQUID = 1;
    private static final int RENDER_ENTITY = 2;
    private static final int RENDER_MODEL = 3;

    private static final BlockGetter NO_WORLD = EmptyBlockGetter.INSTANCE;
    private static final BlockPos ORIGIN = BlockPos.ZERO;
    private static final String VANILLA_PACK_ID = "vanilla";

    /** Asked each time: NeoForge from 1.21.5 makes this before the game exists. */
    private static Minecraft minecraft() {
        return Minecraft.getInstance();
    }
    private final RandomSource random = RandomSource.create(42);

    @Override
    public String minecraftVersion() {
        return SharedConstants.getCurrentVersion().name();
    }

    @Override
    public int dataVersion() {
        return SharedConstants.getCurrentVersion().dataVersion().version();
    }

    @Override
    public List<String> blockIds() {
        List<String> ids = new ArrayList<>();
        for (Identifier id : BuiltInRegistries.BLOCK.keySet()) {
            ids.add(id.toString());
        }
        return ids;
    }

    @Override
    public BlockFacts readBlock(String name, Report report) {
        Block block = BuiltInRegistries.BLOCK.getValue(Identifier.parse(name));
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
        a.put("opaqueCube", json(report.guarded(id, "opaqueCube", () -> state.isSolidRender(), false)));
        a.put("normalCube", json(report.guarded(id, "normalCube", () -> state.isCollisionShapeFullBlock(NO_WORLD, ORIGIN), false)));
        a.put("collidable", json(report.guarded(id, "collidable", () -> !state.getCollisionShape(NO_WORLD, ORIGIN).isEmpty(), true)));
        a.put("opacity", json(report.guarded(id, "opacity", () -> state.getLightDampening(), 15)));
        a.put("brightness", json(report.guarded(id, "brightness", state::getLightEmission, 0)));
        a.put("useNeighborBrightness", json(report.guarded(id, "useNeighborBrightness", state::useShapeForLightOcclusion, false)));
        a.put("hasEntity", json(state.hasBlockEntity()));
        a.put("materialLiquid", json(state.liquid()));
        a.put("materialMapColor", json(report.guarded(id, "materialMapColor", () -> state.getMapColor(NO_WORLD, ORIGIN).col, 0)));
        BlockStateModel model = report.guarded(id, "model",
                () -> minecraft().getModelManager().getBlockStateModelSet().get(state), null);
        a.put("renderLayer", new JsonPrimitive(report.guarded(id, "renderLayer", () -> renderLayer(model), "unknown")));
        a.put("renderType", json(report.guarded(id, "renderType", () -> renderType(state, model), RENDER_MODEL)));
        a.put("cullsSameBlock", json(report.guarded(id, "cullsSameBlock", () -> state.skipRendering(state, Direction.NORTH), false)));

        String particle = "";
        if (model != null) {
            particle = report.guarded(id, "particleTexture", () -> model.particleMaterial().sprite().contents().name().toString(), "");
            boolean tinted = report.guarded(id, "tinted", () -> isTinted(model, state), false);
            a.put("tinted", json(tinted));
            if (tinted) {
                // Without a world, a biome-dependent color provider returns its default color.
                a.put("tintColor", json(report.guarded(id, "tintColor",
                        () -> tintColor(state), -1)));
            }
        }

        // Since 1.21.5 the game no longer names the model of a state, but it is in the block's own blockstate file.
        String resourcePath = BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString();
        StateFacts facts = new StateFacts(stateName(state), a, resourcePath, stateName(state), particle);
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
    private List<Quad> modelQuads(BlockStateModel model, BlockState state) {
        List<Quad> quads = modelQuadsOf(model, state);
        return quads.isEmpty() && state != null ? modelQuadsOf(model, null) : quads;
    }

    private List<Quad> modelQuadsOf(BlockStateModel model, BlockState state) {
        List<Quad> quads = new ArrayList<>();
        List<BlockStateModelPart> parts = new ArrayList<>();
        model.collectParts(RandomSource.create(42), parts);
        for (BlockStateModelPart part : parts) {
            for (Direction direction : Direction.values()) {
                for (BakedQuad quad : part.getQuads(direction)) {
                    quads.add(quad(quad, direction.getName()));
                }
            }
            for (BakedQuad quad : part.getQuads(null)) {
                quads.add(quad(quad, null));
            }
        }
        return quads;
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
        if (minecraft().level != null) {
            entity.setLevel(minecraft().level);
        }
        @SuppressWarnings("unchecked")
        BlockEntityRenderer<BlockEntity, BlockEntityRenderState> renderer =
                (BlockEntityRenderer<BlockEntity, BlockEntityRenderState>) (Object)
                        minecraft().getBlockEntityRenderDispatcher().getRenderer(entity);
        if (renderer == null) {
            return null;
        }
        BlockEntityRenderState renderState = renderer.createRenderState();
        renderer.extractRenderState(entity, renderState, 0, net.minecraft.world.phys.Vec3.ZERO, null);
        Capture capture = new Capture();
        SubmitNodeCollector collector = (SubmitNodeCollector) Proxy.newProxyInstance(
                SubmitNodeCollector.class.getClassLoader(), new Class<?>[]{SubmitNodeCollector.class}, capture);
        renderer.submit(renderState, new PoseStack(), collector,
                new net.minecraft.client.renderer.state.level.CameraRenderState());
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
            Object texture = minecraft().getTextureManager().getTexture(Identifier.parse(name));
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

    /**
     * Takes what a block entity renderer submits, and draws the models, model parts, geometry and block models in
     * it into recorders. Anything else, as text or shadows, is left out.
     */
    private final class Capture implements InvocationHandler {
        private final List<Quad> quads = new ArrayList<>();
        private final List<Recorder> recorders = new ArrayList<>();

        private VertexConsumer buffer(RenderType type) {
            Recorder recorder = new Recorder(textureOf(type), quads);
            recorders.add(recorder);
            return recorder;
        }

        List<Quad> finish() {
            for (Recorder recorder : recorders) {
                recorder.flush();
            }
            return quads;
        }

        @SuppressWarnings("unchecked")
        private <T> T find(Object[] args, Class<T> type) {
            for (Object arg : args) {
                if (type.isInstance(arg)) {
                    return (T) arg;
                }
            }
            return null;
        }

        /** The ints of a submission after the render type, in order: light, overlay, then the tint where it has one. */
        private int tint(Object[] args) {
            int seen = 0;
            for (Object arg : args) {
                if (arg instanceof Integer && ++seen == 3) {
                    return (Integer) arg;
                }
            }
            return -1;
        }

        private VertexConsumer mapped(VertexConsumer buffer, Object[] args) {
            TextureAtlasSprite sprite = find(args, TextureAtlasSprite.class);
            if (sprite != null) {
                return sprite.wrap(buffer);
            }
            return buffer;
        }

        @Override
        @SuppressWarnings({"unchecked", "rawtypes"})
        public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
            if (method.getDeclaringClass() == Object.class) {
                switch (method.getName()) {
                    case "hashCode":
                        return System.identityHashCode(proxy);
                    case "equals":
                        return proxy == args[0];
                    default:
                        return "submissions to keep";
                }
            }
            if (method.getName().equals("order")) {
                return proxy;
            }
            if (method.isDefault()) {
                return InvocationHandler.invokeDefault(proxy, method, args);
            }
            if (args == null) {
                return null;
            }
            PoseStack pose = find(args, PoseStack.class);
            RenderType type = find(args, RenderType.class);
            if (pose == null) {
                return null;
            }
            switch (method.getName()) {
                case "submitModel": {
                    Model model = find(args, Model.class);
                    if (model != null && type != null) {
                        model.setupAnim(args[1]);
                        model.renderToBuffer(pose, mapped(buffer(type), args), 0xF000F0, OverlayTexture.NO_OVERLAY, tint(args));
                    }
                    break;
                }
                case "submitModelPart": {
                    ModelPart part = find(args, ModelPart.class);
                    if (part != null && type != null) {
                        part.render(pose, mapped(buffer(type), args), 0xF000F0, OverlayTexture.NO_OVERLAY, tint(args));
                    }
                    break;
                }
                case "submitCustomGeometry": {
                    SubmitNodeCollector.CustomGeometryRenderer geometry =
                            find(args, SubmitNodeCollector.CustomGeometryRenderer.class);
                    if (geometry != null && type != null) {
                        geometry.render(pose.last(), buffer(type));
                    }
                    break;
                }
                case "submitBlockModel": {
                    List<?> parts = find(args, List.class);
                    if (parts != null) {
                        org.joml.Matrix4f matrix = pose.last().pose();
                        for (Object part : parts) {
                            if (!(part instanceof BlockStateModelPart)) {
                                continue;
                            }
                            List<BakedQuad> faces = new ArrayList<>();
                            for (Direction direction : Direction.values()) {
                                faces.addAll(((BlockStateModelPart) part).getQuads(direction));
                            }
                            faces.addAll(((BlockStateModelPart) part).getQuads(null));
                            for (BakedQuad face : faces) {
                                Quad quad = quad(face, null);
                                for (int i = 0; i < 4; i++) {
                                    org.joml.Vector3f at = matrix.transformPosition(new org.joml.Vector3f(
                                            quad.corners[i * 5], quad.corners[i * 5 + 1], quad.corners[i * 5 + 2]));
                                    quad.corners[i * 5] = at.x();
                                    quad.corners[i * 5 + 1] = at.y();
                                    quad.corners[i * 5 + 2] = at.z();
                                }
                                quads.add(new Quad(quad.corners, quad.texture, Quad.nearestSide(quad.corners), null,
                                        quad.tintIndex, quad.shade, -1));
                            }
                        }
                    }
                    break;
                }
                default:
                    break;
            }
            return null;
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

        @Override
        public VertexConsumer addVertex(float x, float y, float z) {
            if (vertex == 3) {
                flush();
            }
            vertex++;
            corners[vertex * 5] = x;
            corners[vertex * 5 + 1] = y;
            corners[vertex * 5 + 2] = z;
            return this;
        }

        @Override
        public VertexConsumer setColor(int r, int g, int b, int a) {
            if (vertex == 0) {
                color = (a & 0xff) << 24 | (r & 0xff) << 16 | (g & 0xff) << 8 | b & 0xff;
            }
            return this;
        }

        @Override
        public VertexConsumer setColor(int argb) {
            return setColor((argb >> 16) & 0xff, (argb >> 8) & 0xff, argb & 0xff, (argb >>> 24) & 0xff);
        }

        @Override
        public VertexConsumer setUv(float u, float v) {
            if (vertex >= 0) {
                corners[vertex * 5 + 3] = u;
                corners[vertex * 5 + 4] = v;
            }
            return this;
        }

        @Override
        public VertexConsumer setUv1(int u, int v) {
            return this;
        }

        @Override
        public VertexConsumer setUv2(int u, int v) {
            return this;
        }

        public VertexConsumer setUv3(float u, float v) {
            return this;
        }

        @Override
        public VertexConsumer setNormal(float x, float y, float z) {
            if (vertex == 0) {
                nx = x;
                ny = y;
                nz = z;
            }
            return this;
        }

        @Override
        public VertexConsumer setLineWidth(float width) {
            return this;
        }
    }

    private static Quad quad(BakedQuad quad, String cullface) {
        TextureAtlasSprite sprite = quad.materialInfo().sprite();
        float[] corners = new float[20];
        for (int i = 0; i < 4; i++) {
            org.joml.Vector3fc position = quad.position(i);
            long uv = quad.packedUV(i);
            corners[i * 5] = position.x();
            corners[i * 5 + 1] = position.y();
            corners[i * 5 + 2] = position.z();
            corners[i * 5 + 3] = (UVPair.unpackU(uv) - sprite.getU0()) / (sprite.getU1() - sprite.getU0());
            corners[i * 5 + 4] = (UVPair.unpackV(uv) - sprite.getV0()) / (sprite.getV1() - sprite.getV0());
        }
        return new Quad(corners, sprite.contents().name().toString(), quad.direction().getName(), cullface, quad.materialInfo().tintIndex(), true, -1);
    }


    private boolean isTinted(BlockStateModel model, BlockState state) {
        for (BakedQuad quad : quads(model)) {
            if (quad.materialInfo().tintIndex() != -1) {
                return true;
            }
        }
        return false;
    }

    /** Every quad of a model, on every side and none. */
    private List<BakedQuad> quads(BlockStateModel model) {
        List<BakedQuad> quads = new ArrayList<>();
        List<BlockStateModelPart> parts = new ArrayList<>();
        model.collectParts(random, parts);
        for (BlockStateModelPart part : parts) {
            for (Direction direction : Direction.values()) {
                quads.addAll(part.getQuads(direction));
            }
            quads.addAll(part.getQuads(null));
        }
        return quads;
    }

    /**
     * Since 1.21.4 a block drawn by its block entity, such as a chest, has a model like any other, with nothing in
     * it but a particle texture.
     */
    private int renderType(BlockState state, BlockStateModel model) {
        if (state.getRenderShape() != RenderShape.MODEL) {
            // Since 1.13, liquids have no render shape of their own: the fluid is drawn separately.
            return state.liquid() ? RENDER_LIQUID : RENDER_INVISIBLE;
        }
        if (state.hasBlockEntity() && model != null && quads(model).isEmpty()) {
            return RENDER_ENTITY;
        }
        return RENDER_MODEL;
    }



    /**
     * Since 26.1 each quad says which layer it is drawn in, and there are three: solid, cutout and translucent. A
     * block is in the last of these that any of its quads is in.
     */
    private String renderLayer(BlockStateModel model) {
        ChunkSectionLayer layer = ChunkSectionLayer.SOLID;
        for (BakedQuad quad : quads(model)) {
            if (quad.materialInfo().layer().ordinal() > layer.ordinal()) {
                layer = quad.materialInfo().layer();
            }
        }
        return layer.name().toLowerCase(Locale.ROOT);
    }

    /** The color of the first tint, which without a world is the default one of a biome's color. */
    private int tintColor(BlockState state) {
        BlockTintSource source = minecraft().getBlockColors().getTintSource(state, 0);
        return source != null ? source.color(state) : -1;
    }

    @Override
    public List<PackWriter.BlockEntity> readBlockEntities(Predicate<String> include, Report report) {
        List<Block> blocksWithEntities = BuiltInRegistries.BLOCK.stream()
                .filter(block -> block.defaultBlockState().hasBlockEntity())
                .collect(Collectors.toList());
        List<PackWriter.BlockEntity> entities = new ArrayList<>();
        for (BlockEntityType<?> type : BuiltInRegistries.BLOCK_ENTITY_TYPE) {
            Identifier key = BuiltInRegistries.BLOCK_ENTITY_TYPE.getKey(type);
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
                            () -> minecraft().getBlockEntityRenderDispatcher().getRenderer(instance) != null, null);
                    defaultNbt = report.guarded(id, "its default NBT",
                            () -> instance.saveWithFullMetadata(registries()).toString(), null);
                }
            }
            entities.add(new PackWriter.BlockEntity(id, blocks, hasRenderer, defaultNbt));
        }
        return entities;
    }

    /** Registries to save with. Those of a world are complete, but the title screen only has the built-in ones. */
    private net.minecraft.core.HolderLookup.Provider registries() {
        if (minecraft().level != null) {
            return minecraft().level.registryAccess();
        }
        return net.minecraft.core.RegistryAccess.fromRegistryOfRegistries(BuiltInRegistries.REGISTRY);
    }

    /** Biomes come from data packs, so they are only known while in a world. */
    @Override
    public JsonObject readBiomes(Predicate<String> include, Report report) {
        if (minecraft().level == null) {
            return null;
        }
        Registry<Biome> registry = minecraft().level.registryAccess().lookupOrThrow(Registries.BIOME);
        RegistryOps<JsonElement> ops = RegistryOps.create(JsonOps.INSTANCE, minecraft().level.registryAccess());
        JsonObject biomes = new JsonObject();
        for (Map.Entry<net.minecraft.resources.ResourceKey<Biome>, Biome> entry : registry.entrySet()) {
            String id = entry.getKey().identifier().toString();
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
        Object listed = minecraft().getResourceManager().listResources(folder, any -> true);
        Iterable<?> ids = listed instanceof Map ? ((Map<?, ?>) listed).keySet() : (Iterable<?>) listed;
        List<String> names = new ArrayList<>();
        for (Object id : ids) {
            names.add(id.toString());
        }
        return names;
    }

    @Override
    public InputStream openResource(String name) throws IOException {
        Optional<Resource> resource = minecraft().getResourceManager().getResource(Identifier.parse(name));
        return resource.isPresent() ? resource.get().open() : null;
    }

    @Override
    public boolean isGameResource(String name) {
        Optional<Resource> resource = minecraft().getResourceManager().getResource(Identifier.parse(name));
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
