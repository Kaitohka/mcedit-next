package net.mcedit.exporter.neoforge;

import com.mojang.logging.LogUtils;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.core.BlockPos;
import net.minecraft.core.RegistryAccess;
import net.minecraft.core.component.DataComponents;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.ResourceKey;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.Difficulty;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.decoration.ArmorStand;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.GameRules;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.LevelSettings;
import net.minecraft.world.level.WorldDataConfiguration;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.entity.ChestBlockEntity;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.level.levelgen.WorldDimensions;
import net.minecraft.world.level.levelgen.WorldOptions;
import net.minecraft.world.level.levelgen.presets.WorldPresets;
import org.slf4j.Logger;

import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Creates a small world for testing MCEdit, then exits. Started with a system property, like the automatic
 * export. MCEdit's tests look for the blocks and entities placed here, so changing them means changing
 * tests/test_mceditlib/modern_world_test.py too.
 *
 * A name with "debug" in it makes a world of the game's debug type instead, which has every state of every
 * block in a grid at Y=70.
 *
 * A name with "modded" in it also places the default state of every block from a mod in a grid at Y=210, and runs a
 * command given as another system property in the world before exiting, to test commands as a player would use them.
 */
final class TestWorldGenerator {
    static final String PROPERTY = "mcedit.exporter.genTestWorld";
    private static final String COMMAND_PROPERTY = "mcedit.exporter.genTestWorldCommand";
    private static final int MOD_GRID_WIDTH = 48;

    private static final Logger LOGGER = LogUtils.getLogger();
    private static final int CHUNK_RADIUS = 6;
    private static final int DEBUG_CHUNK_RADIUS = 24;
    private static final long SEED = 20261105L;

    private enum Phase { WAITING_FOR_TITLE, WAITING_FOR_WORLD, WRITING, FINISHED }

    private final String worldName;
    private final boolean debugWorld;
    private final AtomicBoolean written = new AtomicBoolean();
    private Phase phase = Phase.WAITING_FOR_TITLE;

    TestWorldGenerator(String worldName) {
        this.worldName = worldName;
        this.debugWorld = worldName.contains("debug");
    }

    void tick(Minecraft minecraft) {
        switch (phase) {
            case WAITING_FOR_TITLE -> {
                if (minecraft.getOverlay() == null && minecraft.screen != null) {
                    create(minecraft);
                    phase = Phase.WAITING_FOR_WORLD;
                }
            }
            case WAITING_FOR_WORLD -> {
                MinecraftServer server = minecraft.getSingleplayerServer();
                if (server != null && minecraft.level != null && minecraft.player != null) {
                    phase = Phase.WRITING;
                    server.execute(() -> write(server));
                }
            }
            case WRITING -> {
                if (written.get()) {
                    phase = Phase.FINISHED;
                    String command = System.getProperty(COMMAND_PROPERTY);
                    if (command != null) {
                        LOGGER.info("Running command: {}", command);
                        minecraft.player.connection.sendCommand(command);
                    }
                    LOGGER.info("Test world {} is written, exiting", worldName);
                    minecraft.stop();
                }
            }
            case FINISHED -> { }
        }
    }

    private void create(Minecraft minecraft) {
        LevelSettings settings = new LevelSettings(worldName, GameType.CREATIVE, false, Difficulty.PEACEFUL, true,
                new GameRules(), WorldDataConfiguration.DEFAULT);
        minecraft.createWorldOpenFlows().createFreshLevel(worldName, settings, new WorldOptions(SEED, true, false),
                debugWorld ? TestWorldGenerator::debugDimensions : WorldPresets::createNormalWorldDimensions,
                new TitleScreen());
    }

    private static WorldDimensions debugDimensions(RegistryAccess registries) {
        return registries.registryOrThrow(Registries.WORLD_PRESET).getHolderOrThrow(WorldPresets.DEBUG).value()
                .createWorldDimensions();
    }

    private void write(MinecraftServer server) {
        try {
            // With the game frozen, fluids and redstone stay as they are placed until the world is saved.
            server.tickRateManager().setFrozen(true);
            int radius = debugWorld ? DEBUG_CHUNK_RADIUS : CHUNK_RADIUS;
            for (ServerLevel level : server.getAllLevels()) {
                int low = debugWorld ? 0 : -radius;
                for (int cx = low; cx < radius; cx++) {
                    for (int cz = low; cz < radius; cz++) {
                        level.getChunk(cx, cz);
                    }
                }
            }
            if (worldName.contains("modded")) {
                placeModBlocks(server.getLevel(Level.OVERWORLD));
            }
            if (!debugWorld) {
                placeOverworld(server.getLevel(Level.OVERWORLD));
                server.getLevel(Level.NETHER).setBlock(new BlockPos(1, 100, 1), Blocks.LAPIS_BLOCK.defaultBlockState(), 2);
                server.getLevel(Level.END).setBlock(new BlockPos(1, 100, 1), Blocks.EMERALD_BLOCK.defaultBlockState(), 2);
            }
            server.saveEverything(false, true, true);
        } catch (RuntimeException e) {
            LOGGER.error("Could not write the test world", e);
        }
        written.set(true);
    }

    private static void placeModBlocks(ServerLevel level) {
        int index = 0;
        for (Map.Entry<ResourceKey<Block>, Block> entry : BuiltInRegistries.BLOCK.entrySet()) {
            ResourceLocation id = entry.getKey().location();
            if (id.getNamespace().equals("minecraft")) {
                continue;
            }
            BlockPos pos = new BlockPos(index % MOD_GRID_WIDTH * 2, 210, index / MOD_GRID_WIDTH * 2);
            index++;
            try {
                level.setBlock(pos, entry.getValue().defaultBlockState(), 2);
            } catch (RuntimeException e) {
                LOGGER.warn("Could not place {}", id, e);
            }
        }
    }

    private static void placeOverworld(ServerLevel level) {
        // Flag 2 tells clients only, so that nothing here reacts to its neighbours.
        level.setBlock(new BlockPos(-1, -60, -1), Blocks.DIAMOND_BLOCK.defaultBlockState(), 2);
        level.setBlock(new BlockPos(2, 319, 2), Blocks.GOLD_BLOCK.defaultBlockState(), 2);

        int y = 200;
        level.setBlock(new BlockPos(0, y, 0), Blocks.CHEST.defaultBlockState(), 2);
        if (level.getBlockEntity(new BlockPos(0, y, 0)) instanceof ChestBlockEntity chest) {
            ItemStack named = new ItemStack(Items.DIAMOND, 5);
            named.set(DataComponents.CUSTOM_NAME, Component.literal("Test Diamonds"));
            chest.setItem(0, named);
            chest.setItem(1, new ItemStack(Items.OAK_LOG, 64));
        }
        level.setBlock(new BlockPos(1, y, 0), Blocks.OAK_SIGN.defaultBlockState(), 2);
        level.setBlock(new BlockPos(2, y, 0),
                Blocks.OAK_STAIRS.defaultBlockState().setValue(BlockStateProperties.WATERLOGGED, true), 2);
        level.setBlock(new BlockPos(3, y, 0),
                Blocks.REDSTONE_LAMP.defaultBlockState().setValue(BlockStateProperties.LIT, true), 2);
        level.setBlock(new BlockPos(5, y, 0), Blocks.WATER.defaultBlockState(), 2);
        level.setBlock(new BlockPos(7, y, 0),
                Blocks.WATER.defaultBlockState().setValue(BlockStateProperties.LEVEL, 3), 2);
        level.setBlock(new BlockPos(9, y, 0), Blocks.LAVA.defaultBlockState(), 2);
        level.setBlock(new BlockPos(11, y - 1, 0), Blocks.STONE.defaultBlockState(), 2);
        level.setBlock(new BlockPos(11, y, 0), Blocks.REDSTONE_WIRE.defaultBlockState(), 2);

        ArmorStand stand = EntityType.ARMOR_STAND.create(level);
        stand.setPos(0.5, y + 2, 0.5);
        stand.setNoGravity(true);
        stand.setCustomName(Component.literal("Test Stand"));
        level.addFreshEntity(stand);

        Mob pig = EntityType.PIG.create(level);
        pig.setPos(-8.5, y + 2, -8.5);
        pig.setNoAi(true);
        pig.setNoGravity(true);
        pig.setPersistenceRequired();
        level.addFreshEntity(pig);
    }
}
