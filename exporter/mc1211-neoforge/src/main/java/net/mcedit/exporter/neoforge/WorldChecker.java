package net.mcedit.exporter.neoforge;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.mojang.logging.LogUtils;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.ResourceKey;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.LightLayer;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.levelgen.Heightmap;
import org.slf4j.Logger;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * For testing that the game accepts a world that MCEdit has saved: opens the world named by a system property, reports
 * what the game finds at the places listed in a request file, saves the world and exits. The tests compare the report
 * with what was saved, and read what the game wrote back.
 *
 * The request is run/mcedit_check_request.json: {"blocks": [{"dimension": "minecraft:overworld", "x": 0, "y": 0, "z": 0}],
 * "entityChunks": [{"dimension": "minecraft:overworld", "x": 0, "z": 0}]}. The report is run/mcedit_check_report.json.
 */
final class WorldChecker {
    static final String PROPERTY = "mcedit.exporter.checkWorld";

    private static final Logger LOGGER = LogUtils.getLogger();
    /** Long enough for chunks that need their light worked out to get it done. */
    private static final int TICKS_TO_SETTLE = 100;

    private enum Phase { WAITING_FOR_TITLE, WAITING_FOR_WORLD, SETTLING, CHECKING, FINISHED }

    private final String worldName;
    private final AtomicBoolean checked = new AtomicBoolean();
    private Phase phase = Phase.WAITING_FOR_TITLE;
    private int ticks;

    WorldChecker(String worldName) {
        this.worldName = worldName;
    }

    void tick(Minecraft minecraft) {
        switch (phase) {
            case WAITING_FOR_TITLE -> {
                if (minecraft.getOverlay() == null && minecraft.screen != null) {
                    minecraft.createWorldOpenFlows().openWorld(worldName, () -> {
                        LOGGER.error("Could not open world {}", worldName);
                        minecraft.setScreen(new TitleScreen());
                        minecraft.stop();
                    });
                    phase = Phase.WAITING_FOR_WORLD;
                }
            }
            case WAITING_FOR_WORLD -> {
                MinecraftServer server = minecraft.getSingleplayerServer();
                if (server != null && minecraft.level != null && minecraft.player != null) {
                    phase = Phase.SETTLING;
                    server.execute(() -> loadRequestedChunks(minecraft, server));
                }
            }
            case SETTLING -> {
                if (++ticks >= TICKS_TO_SETTLE) {
                    phase = Phase.CHECKING;
                    MinecraftServer server = minecraft.getSingleplayerServer();
                    server.execute(() -> check(minecraft, server));
                }
            }
            case CHECKING -> {
                if (checked.get()) {
                    phase = Phase.FINISHED;
                    LOGGER.info("World check of {} is done, exiting", worldName);
                    minecraft.stop();
                }
            }
            case FINISHED -> { }
        }
    }

    private static JsonObject readRequest(Minecraft minecraft) throws IOException {
        Path file = minecraft.gameDirectory.toPath().resolve("mcedit_check_request.json");
        return JsonParser.parseString(Files.readString(file, StandardCharsets.UTF_8)).getAsJsonObject();
    }

    private static ServerLevel level(MinecraftServer server, JsonObject place) {
        ResourceKey<Level> key = ResourceKey.create(Registries.DIMENSION, ResourceLocation.parse(place.get("dimension").getAsString()));
        return server.getLevel(key);
    }

    /** Loaded chunks with a ticket stay loaded, so that their light can be worked out while settling. */
    private void loadRequestedChunks(Minecraft minecraft, MinecraftServer server) {
        try {
            JsonObject request = readRequest(minecraft);
            for (JsonElement element : request.getAsJsonArray("blocks")) {
                JsonObject place = element.getAsJsonObject();
                ServerLevel level = level(server, place);
                level.setChunkForced(place.get("x").getAsInt() >> 4, place.get("z").getAsInt() >> 4, true);
            }
            for (JsonElement element : request.getAsJsonArray("entityChunks")) {
                JsonObject place = element.getAsJsonObject();
                level(server, place).setChunkForced(place.get("x").getAsInt(), place.get("z").getAsInt(), true);
            }
        } catch (IOException | RuntimeException e) {
            LOGGER.error("Could not load the chunks to check", e);
        }
    }

    private void check(Minecraft minecraft, MinecraftServer server) {
        try {
            JsonObject request = readRequest(minecraft);
            JsonArray blocks = new JsonArray();
            for (JsonElement element : request.getAsJsonArray("blocks")) {
                JsonObject place = element.getAsJsonObject();
                ServerLevel level = level(server, place);
                BlockPos pos = new BlockPos(place.get("x").getAsInt(), place.get("y").getAsInt(), place.get("z").getAsInt());
                JsonObject found = place.deepCopy();
                found.addProperty("state", level.getBlockState(pos).toString());
                found.addProperty("skyLight", level.getBrightness(LightLayer.SKY, pos));
                found.addProperty("blockLight", level.getBrightness(LightLayer.BLOCK, pos));
                found.addProperty("surface", level.getHeight(Heightmap.Types.WORLD_SURFACE, pos.getX(), pos.getZ()));
                BlockEntity blockEntity = level.getBlockEntity(pos);
                if (blockEntity != null) {
                    found.addProperty("blockEntity", BuiltInRegistries.BLOCK_ENTITY_TYPE.getKey(blockEntity.getType()).toString());
                }
                blocks.add(found);
            }

            JsonArray entities = new JsonArray();
            for (JsonElement element : request.getAsJsonArray("entityChunks")) {
                JsonObject place = element.getAsJsonObject();
                ServerLevel level = level(server, place);
                ChunkPos chunk = new ChunkPos(place.get("x").getAsInt(), place.get("z").getAsInt());
                for (Entity entity : level.getAllEntities()) {
                    if (entity.chunkPosition().equals(chunk)) {
                        JsonObject found = new JsonObject();
                        found.addProperty("id", BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).toString());
                        found.addProperty("name", entity.hasCustomName() ? entity.getCustomName().getString() : "");
                        found.addProperty("uuid", entity.getStringUUID());
                        found.addProperty("x", entity.getX());
                        found.addProperty("y", entity.getY());
                        found.addProperty("z", entity.getZ());
                        entities.add(found);
                    }
                }
            }

            JsonObject report = new JsonObject();
            report.addProperty("levelName", server.getWorldData().getLevelName());
            report.add("blocks", blocks);
            report.add("entities", entities);
            Gson gson = new GsonBuilder().setPrettyPrinting().create();
            Files.writeString(minecraft.gameDirectory.toPath().resolve("mcedit_check_report.json"), gson.toJson(report),
                    StandardCharsets.UTF_8);

            for (ServerLevel level : server.getAllLevels()) {
                level.getForcedChunks().forEach(chunk -> level.setChunkForced(ChunkPos.getX(chunk), ChunkPos.getZ(chunk), false));
            }
            server.saveEverything(false, true, true);
        } catch (IOException | RuntimeException e) {
            LOGGER.error("Could not check the world", e);
        }
        checked.set(true);
    }
}
