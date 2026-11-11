package @PACKAGE@;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import cpw.mods.fml.common.Loader;
import net.mcedit.exporter.export.FeedbackCapture;
import net.mcedit.exporter.export.Game;
import net.mcedit.exporter.export.Quad;
import net.mcedit.exporter.export.Report;
import net.mcedit.exporter.pack.PackWriter;
import net.minecraft.block.Block;
import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.RenderBlocks;
import net.minecraft.client.renderer.Tessellator;
import net.minecraft.client.renderer.texture.ITextureObject;
import net.minecraft.client.renderer.texture.TextureAtlasSprite;
import net.minecraft.client.renderer.texture.TextureManager;
import net.minecraft.client.renderer.tileentity.TileEntitySpecialRenderer;
import net.minecraft.client.renderer.texture.TextureMap;
import net.minecraft.client.shader.TesselatorVertexState;
import net.minecraft.init.Blocks;
import net.minecraft.world.IBlockAccess;
import net.minecraft.world.biome.BiomeGenBase;
import net.minecraftforge.common.util.ForgeDirection;
import net.minecraft.client.renderer.tileentity.TileEntityRendererDispatcher;
import net.minecraft.nbt.NBTTagCompound;
import net.minecraft.tileentity.TileEntity;
import net.minecraft.util.IIcon;
import net.minecraft.util.ResourceLocation;

import java.io.ByteArrayInputStream;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.io.InputStream;
import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.function.Predicate;

/**
 * Reads blocks from Minecraft 1.7.10, in Forge, with MCP's names for the game. The mod loader's part is in Glue.
 *
 * This version has no block states and no model files: a block is a number and a data value, and code draws it,
 * with a texture for each side and data value. Each data value is exported as a state, "meta=N", and is given a
 * blockstate file and a model made here from the textures the game gives each side, which MCEdit then reads like
 * any other. A block the game draws as a plain cube gets a cube, one drawn as crossed squares, such as a flower,
 * gets those, and any other gets a cube too, which is the best that can be done without its code.
 */
abstract class VersionGame implements Game {
    private static final int RENDER_INVISIBLE = 0;
    private static final int RENDER_LIQUID = 1;
    private static final int RENDER_MODEL = 3;

    // The game's own render types, from RenderBlocks.
    private static final int GAME_RENDER_CUBE = 0;
    private static final int GAME_RENDER_CROSSED = 1;
    private static final int GAME_RENDER_LIQUID = 4;
    private static final int GAME_RENDER_INVISIBLE = -1;

    private static final String[] SIDES = {"down", "up", "north", "south", "west", "east"};
    private static final List<String> METAS = new ArrayList<>();

    static {
        for (int meta = 0; meta < 16; meta++) {
            METAS.add(String.valueOf(meta));
        }
    }

    private final Minecraft minecraft = Minecraft.getMinecraft();
    private final Gson gson = new GsonBuilder().disableHtmlEscaping().create();
    /** The blockstate files and models made here, as "namespace:path" of the file, so that they can be read back. */
    private final Map<String, byte[]> generated = new HashMap<>();

    @Override
    public String minecraftVersion() {
        return Loader.MC_VERSION;
    }

    @Override
    public int dataVersion() {
        return 0;
    }

    @Override
    public List<String> blockIds() {
        List<String> ids = new ArrayList<>();
        for (Object key : Block.blockRegistry.getKeys()) {
            String name = key.toString();
            ids.add(name.indexOf(':') < 0 ? "minecraft:" + name : name);
        }
        return ids;
    }

    @Override
    public BlockFacts readBlock(String name, Report report) {
        Block block = (Block) Block.blockRegistry.getObject(name);
        Map<String, List<String>> properties = new LinkedHashMap<>();
        properties.put("meta", METAS);

        int renderType = report.guarded(name, "renderType", block::getRenderType, GAME_RENDER_CUBE);
        String location = generatedLocation(name);
        JsonObject variants = new JsonObject();
        List<StateFacts> states = new ArrayList<>();
        JsonObject metaStates = new JsonObject();
        for (int meta = 0; meta < 16; meta++) {
            String state = "meta=" + meta;
            metaStates.addProperty(String.valueOf(meta), state);
            String[] icons = icons(name, block, meta, report);
            // Blockstate files before 1.13 name their models from models/block/, which is where this one goes.
            String model = location.replace(":", ":mcedit_generated/") + "_" + meta;
            int value = meta;
            int color = report.guarded(name, "tintColor", () -> block.getRenderColor(value), 0xffffff);
            makeModel(model.replace(":", ":block/"), renderType, icons, (color & 0xffffff) != 0xffffff);
            JsonObject variant = new JsonObject();
            variant.addProperty("model", model);
            variants.add(state, variant);
            states.add(readState(name, block, meta, renderType, location, state, icons, report));
        }
        JsonObject blockstate = new JsonObject();
        blockstate.add("variants", variants);
        put(file(location, "blockstates/", ".json"), blockstate);

        String displayName = report.guarded(name, "displayName", block::getLocalizedName, name);
        String descriptionId = report.guarded(name, "descriptionId", block::getUnlocalizedName, "");
        BlockFacts facts = new BlockFacts(name, displayName, descriptionId, "meta=0", properties, states);
        JsonObject legacy = new JsonObject();
        legacy.addProperty("numericId", Block.getIdFromBlock(block));
        legacy.add("metaStates", metaStates);
        facts.legacy = legacy;
        return facts;
    }

    private StateFacts readState(String id, Block block, int meta, int renderType, String location, String state,
                                 String[] icons, Report report) {
        Map<String, JsonElement> a = new LinkedHashMap<>();
        a.put("opaqueCube", json(report.guarded(id, "opaqueCube", block::isOpaqueCube, false)));
        a.put("normalCube", json(report.guarded(id, "normalCube", block::isNormalCube, false)));
        a.put("collidable", json(report.guarded(id, "collidable", () -> block.getMaterial().blocksMovement(), true)));
        // 0 to 255 in this version, where later ones and MCEdit have 0 to 15
        a.put("opacity", json(Math.min(15, report.guarded(id, "opacity", block::getLightOpacity, 15))));
        a.put("brightness", json(report.guarded(id, "brightness", block::getLightValue, 0)));
        a.put("useNeighborBrightness", json(report.guarded(id, "useNeighborBrightness", block::getUseNeighborBrightness, false)));
        a.put("hasEntity", json(report.guarded(id, "hasEntity", () -> block.hasTileEntity(meta), false)));
        a.put("materialLiquid", json(block.getMaterial().isLiquid()));
        a.put("materialMapColor", json(report.guarded(id, "materialMapColor", () -> block.getMapColor(meta).colorValue, 0)));
        int pass = report.guarded(id, "renderLayer", block::getRenderBlockPass, 0);
        a.put("renderLayer", new JsonPrimitive(pass == 1 ? "translucent" : block.isOpaqueCube() ? "solid" : "cutout"));
        int ours;
        if (renderType == GAME_RENDER_INVISIBLE) {
            ours = RENDER_INVISIBLE;
        } else if (renderType == GAME_RENDER_LIQUID || block.getMaterial().isLiquid()) {
            ours = RENDER_LIQUID;
        } else {
            ours = RENDER_MODEL;
        }
        a.put("renderType", json(ours));
        a.put("meta", json(meta));
        a.put("gameRenderType", json(renderType));
        int color = report.guarded(id, "tintColor", () -> block.getRenderColor(meta), 0xffffff);
        a.put("tinted", json((color & 0xffffff) != 0xffffff));
        if ((color & 0xffffff) != 0xffffff) {
            a.put("tintColor", json(color));
        }
        StateFacts facts = new StateFacts(state, a, location, state, plainTexture(icons[2]));
        if (drawnByCode(id, renderType)) {
            // The cube made from its textures stands in if what its renderer draws can't be read.
            facts.modelQuads = () -> drawnQuads(block, meta);
            facts.customModel = true;
        }
        if (!id.startsWith("minecraft:") && report.guarded(id, "hasEntity", () -> block.hasTileEntity(meta), false)) {
            facts.entityQuads = () -> entityQuads(block, meta);
        }
        return facts;
    }

    /**
     * Whether a block from a mod is drawn by a renderer of its own, which only its code can say the look of. What its
     * renderer draws is kept in place of the cube made here.
     */
    private static boolean drawnByCode(String id, int renderType) {
        return !id.startsWith("minecraft:") && renderType != GAME_RENDER_CUBE && renderType != GAME_RENDER_CROSSED
                && renderType != GAME_RENDER_INVISIBLE && renderType != GAME_RENDER_LIQUID;
    }

    private final OneBlock oneBlock = new OneBlock();

    /**
     * What the game's block renderer draws for a block with this data value, alone in a world of air. It draws into
     * the tessellator, whose vertices are read back before they are drawn.
     */
    private List<Quad> drawnQuads(Block block, int meta) {
        oneBlock.block = block;
        oneBlock.meta = meta;
        Tessellator tessellator = Tessellator.instance;
        tessellator.startDrawingQuads();
        TesselatorVertexState drawn = null;
        try {
            new RenderBlocks(oneBlock).renderBlockByRenderType(block, 0, 0, 0);
            drawn = tessellator.getVertexState(0, 0, 0);
        } catch (RuntimeException e) {
            // nothing drawn, or the renderer needs more than a world of air
        } finally {
            tessellator.draw();
        }
        List<Quad> quads = new ArrayList<>();
        if (drawn == null || !drawn.getHasTexture()) {
            return quads;
        }
        int[] data = drawn.getRawBuffer();
        for (int start = 0; start + 32 <= drawn.getRawBufferIndex(); start += 32) {
            float[] corners = new float[20];
            float u = 0;
            float v = 0;
            for (int i = 0; i < 4; i++) {
                for (int j = 0; j < 5; j++) {
                    corners[i * 5 + j] = Float.intBitsToFloat(data[start + i * 8 + j]);
                }
                u += corners[i * 5 + 3] / 4;
                v += corners[i * 5 + 4] / 4;
            }
            TextureAtlasSprite sprite = blockSpriteAt(u, v);
            if (sprite == null) {
                continue;
            }
            for (int i = 0; i < 4; i++) {
                corners[i * 5 + 3] = (corners[i * 5 + 3] - sprite.getMinU()) / (sprite.getMaxU() - sprite.getMinU());
                corners[i * 5 + 4] = (corners[i * 5 + 4] - sprite.getMinV()) / (sprite.getMaxV() - sprite.getMinV());
            }
            quads.add(new Quad(corners, textureName(sprite.getIconName()), Quad.nearestSide(corners), cullface(corners),
                    -1, true, -1));
        }
        return quads;
    }

    /** The side of the block a face lies on, which a neighbor there hides, or null. */
    private static String cullface(float[] c) {
        String[] sides = {"west", "east", "down", "up", "north", "south"};
        for (int axis = 0; axis < 3; axis++) {
            for (int end = 0; end < 2; end++) {
                boolean on = true;
                for (int i = 0; i < 4; i++) {
                    on &= Math.abs(c[i * 5 + axis] - end) < 1e-5;
                }
                if (on) {
                    return sides[axis * 2 + end];
                }
            }
        }
        return null;
    }

    private List<TextureAtlasSprite> blockSprites;

    /** The sprite of the block atlas at a place on it. The atlas keeps them in a private map, found by its type. */
    private TextureAtlasSprite blockSpriteAt(float u, float v) {
        if (blockSprites == null) {
            blockSprites = new ArrayList<>();
            Object atlas = minecraft.getTextureManager().getTexture(TextureMap.locationBlocksTexture);
            for (Field field : TextureMap.class.getDeclaredFields()) {
                if (!Map.class.isAssignableFrom(field.getType()) || java.lang.reflect.Modifier.isStatic(field.getModifiers())) {
                    continue;
                }
                try {
                    field.setAccessible(true);
                    for (Object value : ((Map<?, ?>) field.get(atlas)).values()) {
                        if (value instanceof TextureAtlasSprite && !blockSprites.contains(value)) {
                            blockSprites.add((TextureAtlasSprite) value);
                        }
                    }
                } catch (ReflectiveOperationException | RuntimeException e) {
                    continue;
                }
            }
        }
        // The smallest sprite there: one whose file was missing can have bounds over the whole atlas.
        TextureAtlasSprite found = null;
        float area = Float.MAX_VALUE;
        for (TextureAtlasSprite sprite : blockSprites) {
            float size = (sprite.getMaxU() - sprite.getMinU()) * (sprite.getMaxV() - sprite.getMinV());
            if (u >= sprite.getMinU() && u <= sprite.getMaxU() && v >= sprite.getMinV() && v <= sprite.getMaxV()
                    && size < area) {
                found = sprite;
                area = size;
            }
        }
        return found;
    }

    /** A world with one block in it, at the origin, and air everywhere else. */
    private static final class OneBlock implements IBlockAccess {
        Block block;
        int meta;

        private static boolean here(int x, int y, int z) {
            return x == 0 && y == 0 && z == 0;
        }

        @Override
        public Block getBlock(int x, int y, int z) {
            return here(x, y, z) ? block : Blocks.air;
        }

        @Override
        public TileEntity getTileEntity(int x, int y, int z) {
            return null;
        }

        @Override
        public int getLightBrightnessForSkyBlocks(int x, int y, int z, int light) {
            return 15 << 20 | 15 << 4;
        }

        @Override
        public int getBlockMetadata(int x, int y, int z) {
            return here(x, y, z) ? meta : 0;
        }

        @Override
        public int isBlockProvidingPowerTo(int x, int y, int z, int side) {
            return 0;
        }

        @Override
        public boolean isAirBlock(int x, int y, int z) {
            return !here(x, y, z);
        }

        @Override
        public BiomeGenBase getBiomeGenForCoords(int x, int z) {
            return BiomeGenBase.plains;
        }

        @Override
        public int getHeight() {
            return 256;
        }

        @Override
        public boolean extendedLevelsInChunkCache() {
            return false;
        }

        @Override
        public boolean isSideSolid(int x, int y, int z, ForgeDirection side, boolean otherwise) {
            return here(x, y, z) && block.isSideSolid(this, x, y, z, side);
        }
    }

    /**
     * The texture of each side for a data value, as "namespace:blocks/name", or "" where the game gives none. A data
     * value the block does not use often throws, and that is not a problem with the export.
     */
    private String[] icons(String id, Block block, int meta, Report report) {
        String[] names = new String[6];
        oneBlock.block = block;
        oneBlock.meta = meta;
        for (int side = 0; side < 6; side++) {
            // As in a world first, alone in one: some blocks, as Chisel's, have no texture of their own without one.
            final int face = side;
            String name = iconName(() -> block.getIcon(oneBlock, 0, 0, 0, face));
            if (name.isEmpty() || name.endsWith(":missingno") || name.endsWith("/missingno")) {
                String plain = iconName(() -> block.getIcon(face, meta));
                if (!plain.isEmpty()) {
                    name = plain;
                }
            }
            names[side] = name;
        }
        return names;
    }

    private String iconName(java.util.function.Supplier<IIcon> get) {
        try {
            IIcon icon = get.get();
            if (icon == null || icon.getIconName() == null) {
                return "";
            }
            String name = icon.getIconName();
            int bar = name.indexOf('|');
            if (bar >= 0) {
                return cellOf(name.substring(0, bar), icon);
            }
            return textureName(name);
        } catch (RuntimeException e) {
            return "";
        }
    }

    /**
     * An icon that is a cell of a sprite, as Chisel's "name|x.y" are, as the sprite's texture and where the cell is
     * on it in sixteenths: "chisel:blocks/factory/plating-v9@0,0,5.333,5.333". Without the sprite, the icon's name.
     */
    private String cellOf(String spriteName, IIcon icon) {
        blockSpriteAt(-1, -1);
        for (TextureAtlasSprite sprite : blockSprites) {
            if (!spriteName.equals(sprite.getIconName())) {
                continue;
            }
            float width = sprite.getMaxU() - sprite.getMinU();
            float height = sprite.getMaxV() - sprite.getMinV();
            if (width <= 0 || height <= 0) {
                break;
            }
            return String.format(java.util.Locale.ROOT, "%s@%.4f,%.4f,%.4f,%.4f", textureName(spriteName),
                    16 * (icon.getMinU() - sprite.getMinU()) / width, 16 * (icon.getMinV() - sprite.getMinV()) / height,
                    16 * (icon.getMaxU() - sprite.getMinU()) / width, 16 * (icon.getMaxV() - sprite.getMinV()) / height);
        }
        return textureName(icon.getIconName());
    }

    /** A texture as icons() gives it, without the cell of it a face shows. */
    private static String plainTexture(String icon) {
        int at = icon.indexOf('@');
        return at < 0 ? icon : icon.substring(0, at);
    }

    /** The cell of its texture a face shows, as uv in sixteenths, or null for all of it. */
    private static JsonArray cellUv(String icon) {
        int at = icon.indexOf('@');
        if (at < 0) {
            return null;
        }
        String[] parts = icon.substring(at + 1).split(",");
        double[] uv = new double[4];
        for (int i = 0; i < 4; i++) {
            uv[i] = Math.round(Double.parseDouble(parts[i]) * 1000) / 1000.0;
        }
        return numbers(uv);
    }

    /** "somemod:thing" in 1.7.10 is the file "somemod:textures/blocks/thing.png", and "somemod:blocks/thing" later. */
    private static String textureName(String iconName) {
        if (iconName == null || iconName.isEmpty()) {
            return "";
        }
        int colon = iconName.indexOf(':');
        // The game makes the namespace lower case when it finds the file, as "Chisel:x" is in assets/chisel/.
        String namespace = colon < 0 ? "minecraft" : iconName.substring(0, colon).toLowerCase(java.util.Locale.ROOT);
        String path = colon < 0 ? iconName : iconName.substring(colon + 1);
        return namespace + ":blocks/" + path;
    }

    /**
     * A model for one data value of a block. It is written out in full, with no parent, as this version of the game
     * has no model files for a parent to be. The game's code tints the textures of some blocks, such as grass.
     */
    private void makeModel(String model, int renderType, String[] icons, boolean tinted) {
        JsonObject textures = new JsonObject();
        JsonArray elements = new JsonArray();
        String particle = plainTexture(!icons[2].isEmpty() ? icons[2] : !icons[1].isEmpty() ? icons[1] : icons[0]);
        if (particle.isEmpty()) {
            return;  // nothing to draw, as with air
        }
        if (renderType == GAME_RENDER_CROSSED) {
            textures.addProperty("cross", particle);
            elements.add(crossPlane(new double[]{0.8, 0, 8}, new double[]{15.2, 16, 8}, 45, "north", "south", tinted));
            elements.add(crossPlane(new double[]{8, 0, 0.8}, new double[]{8, 16, 15.2}, 45, "west", "east", tinted));
        } else {
            JsonObject faces = new JsonObject();
            for (int side = 0; side < 6; side++) {
                textures.addProperty(SIDES[side], !icons[side].isEmpty() ? plainTexture(icons[side]) : particle);
                JsonObject face = new JsonObject();
                JsonArray uv = cellUv(icons[side]);
                if (uv != null) {
                    face.add("uv", uv);
                }
                face.addProperty("texture", "#" + SIDES[side]);
                face.addProperty("cullface", SIDES[side]);
                if (tinted) {
                    face.addProperty("tintindex", 0);
                }
                faces.add(SIDES[side], face);
            }
            JsonObject cube = new JsonObject();
            cube.add("from", numbers(0, 0, 0));
            cube.add("to", numbers(16, 16, 16));
            cube.add("faces", faces);
            elements.add(cube);
        }
        textures.addProperty("particle", particle);
        JsonObject json = new JsonObject();
        json.add("textures", textures);
        json.add("elements", elements);
        put(file(model, "models/", ".json"), json);
    }

    /** One of the two planes of a block drawn as crossed squares, as the game's own cross model has them. */
    private static JsonObject crossPlane(double[] from, double[] to, int angle, String front, String back, boolean tinted) {
        JsonObject element = new JsonObject();
        element.add("from", numbers(from));
        element.add("to", numbers(to));
        JsonObject rotation = new JsonObject();
        rotation.add("origin", numbers(8, 8, 8));
        rotation.addProperty("axis", "y");
        rotation.addProperty("angle", angle);
        rotation.addProperty("rescale", true);
        element.add("rotation", rotation);
        element.addProperty("shade", false);
        JsonObject faces = new JsonObject();
        for (String side : new String[]{front, back}) {
            JsonObject face = new JsonObject();
            face.add("uv", numbers(0, 0, 16, 16));
            face.addProperty("texture", "#cross");
            if (tinted) {
                face.addProperty("tintindex", 0);
            }
            faces.add(side, face);
        }
        element.add("faces", faces);
        return element;
    }

    private static JsonArray numbers(double... values) {
        JsonArray array = new JsonArray();
        for (double value : values) {
            array.add(new JsonPrimitive(value));
        }
        return array;
    }

    /** Where a block's made-up blockstate file goes: its own name, in its mod's namespace. */
    private static String generatedLocation(String name) {
        if (name.indexOf(':') < 0) {
            return "minecraft:" + name;
        }
        // The game finds files with the namespace in lower case, as "IronChest:x" in assets/ironchest/.
        int colon = name.indexOf(':');
        return name.substring(0, colon).toLowerCase(java.util.Locale.ROOT) + name.substring(colon);
    }

    private static String file(String location, String folder, String extension) {
        int colon = location.indexOf(':');
        return location.substring(0, colon + 1) + folder + location.substring(colon + 1) + extension;
    }

    private void put(String file, JsonObject json) {
        generated.put(file, gson.toJson(json).getBytes(StandardCharsets.UTF_8));
    }

    @Override
    public List<PackWriter.BlockEntity> readBlockEntities(Predicate<String> include, Report report) {
        Map<String, List<String>> blocksByEntity = new TreeMap<>();
        Map<String, TileEntity> instances = new HashMap<>();
        Map<Class<?>, String> namesByClass = tileEntityNames();
        for (String blockId : blockIds()) {
            Block block = (Block) Block.blockRegistry.getObject(blockId);
            if (block == null || !report.guarded(blockId, "hasEntity", () -> block.hasTileEntity(0), false)) {
                continue;
            }
            TileEntity instance = report.guarded(blockId, "a new block entity", () -> block.createTileEntity(null, 0), null);
            if (instance == null) {
                continue;
            }
            String entityId = withNamespace(namesByClass.get(instance.getClass()), blockId);
            if (entityId == null || !include.test(entityId)) {
                continue;
            }
            blocksByEntity.computeIfAbsent(entityId, key -> new ArrayList<>()).add(blockId);
            instances.putIfAbsent(entityId, instance);
        }
        List<PackWriter.BlockEntity> entities = new ArrayList<>();
        for (Map.Entry<String, List<String>> entry : blocksByEntity.entrySet()) {
            String id = entry.getKey();
            TileEntity instance = instances.get(id);
            Boolean hasRenderer = report.guarded(id, "whether it has a renderer",
                    () -> TileEntityRendererDispatcher.instance.hasSpecialRenderer(instance), null);
            String defaultNbt = report.guarded(id, "its default NBT", () -> {
                NBTTagCompound tag = new NBTTagCompound();
                instance.writeToNBT(tag);
                return tag.toString();
            }, null);
            List<String> blocks = new ArrayList<>(entry.getValue());
            blocks.sort(null);
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

    @Override
    public JsonObject readBiomes(Predicate<String> include, Report report) {
        return null;
    }

    @Override
    public InputStream openResource(String name) throws IOException {
        byte[] made = generated.get(name);
        if (made != null) {
            return new ByteArrayInputStream(made);
        }
        try {
            return minecraft.getResourceManager().getResource(new ResourceLocation(name)).getInputStream();
        } catch (FileNotFoundException e) {
            return null;
        }
    }

    /** MCEdit shows worlds of this version with the textures of 1.12, which has the same names for them. */
    @Override
    public boolean isGameResource(String name) {
        return name.startsWith("minecraft:") && !generated.containsKey(name);
    }

    private static JsonElement json(boolean value) {
        return new JsonPrimitive(value);
    }

    private static JsonElement json(int value) {
        return new JsonPrimitive(value);
    }

    /**
     * What the block entity renderer of a block with this data value draws, with a block entity made for it and
     * nothing else. Renderers in this version draw with OpenGL itself, so it is read back with OpenGL's feedback mode.
     */
    private List<Quad> entityQuads(Block block, int meta) {
        TileEntity entity = block.createTileEntity(null, meta);
        if (entity == null) {
            return null;
        }
        TileEntitySpecialRenderer renderer = TileEntityRendererDispatcher.instance.getSpecialRenderer(entity);
        if (renderer == null) {
            return null;
        }
        // The block and data value the renderer asks the block entity for, which would come from a world.
        entity.blockType = block;
        entity.blockMetadata = meta;
        List<FeedbackCapture.Sprite> sprites = new ArrayList<>();
        blockSpriteAt(0, 0);
        for (TextureAtlasSprite sprite : blockSprites) {
            sprites.add(new FeedbackCapture.Sprite(textureName(sprite.getIconName()), sprite.getMinU(),
                    sprite.getMaxU(), sprite.getMinV(), sprite.getMaxV()));
        }
        // In a world of its one block first, as many renderers look there for what to draw; then in none, as some
        // ask a world more than that one can answer. The world is the game's too while they draw, as before a world
        // is joined there is none.
        RuntimeException inWorld = null;
        OneBlockWorld world = FeedbackCapture.allocate(OneBlockWorld.class);
        if (world != null) {
            world.block = block;
            world.meta = meta;
            world.entity = entity;
            entity.setWorldObj(world);
            boolean gameWorld = minecraft.theWorld == null;
            if (gameWorld) {
                minecraft.theWorld = world;
            }
            try {
                return drawnByRenderer(renderer, entity, sprites);
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
            return drawnByRenderer(renderer, entity, sprites);
        } catch (RuntimeException e) {
            // What went wrong in its world says more than what went wrong in none.
            throw inWorld != null ? inWorld : e;
        }
    }

    private List<Quad> drawnByRenderer(TileEntitySpecialRenderer renderer, TileEntity entity,
                                       List<FeedbackCapture.Sprite> sprites) {
        FeedbackCapture capture = new FeedbackCapture();
        TextureManager real = minecraft.getTextureManager();
        // Before a world is drawn, the renderers have no texture manager or font to draw with.
        FeedbackCapture.fillEmptyFields(TileEntityRendererDispatcher.instance, minecraft);
        Runnable restore = FeedbackCapture.swap(real, new MarkingTextures(real, capture), minecraft,
                TileEntityRendererDispatcher.instance);
        try {
            return FeedbackCapture.quads(capture.capture("minecraft:textures/atlas/blocks.png", () -> {
                try {
                    renderer.renderTileEntityAt(entity, 0, 0, 0, 0);
                } catch (RuntimeException e) {
                    finishDrawing();
                    throw e;
                }
            }), sprites);
        } finally {
            restore.run();
        }
    }

    /**
     * A world with one block in it, at the origin, made without its constructor: renderers ask it for the block,
     * data value and block entity there, and its light and time, which it answers itself.
     */
    private static final class OneBlockWorld extends net.minecraft.client.multiplayer.WorldClient {
        Block block;
        int meta;
        TileEntity entity;

        private OneBlockWorld() {
            super(null, null, 0, null, null);
        }

        private static boolean here(int x, int y, int z) {
            return x == 0 && y == 0 && z == 0;
        }

        @Override
        public Block getBlock(int x, int y, int z) {
            return here(x, y, z) ? block : Blocks.air;
        }

        @Override
        public int getBlockMetadata(int x, int y, int z) {
            return here(x, y, z) ? meta : 0;
        }

        @Override
        public TileEntity getTileEntity(int x, int y, int z) {
            return here(x, y, z) ? entity : null;
        }

        @Override
        public boolean isAirBlock(int x, int y, int z) {
            return !here(x, y, z);
        }

        @Override
        public int getLightBrightnessForSkyBlocks(int x, int y, int z, int light) {
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
            Tessellator.instance.draw();
        } catch (RuntimeException ignored) {
            // it was not drawing
        }
    }
}
