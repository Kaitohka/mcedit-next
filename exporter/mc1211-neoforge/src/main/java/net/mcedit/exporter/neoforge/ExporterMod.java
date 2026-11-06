package net.mcedit.exporter.neoforge;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.context.CommandContext;
import com.mojang.logging.LogUtils;
import net.minecraft.client.Minecraft;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.client.event.ClientTickEvent;
import net.neoforged.neoforge.client.event.RegisterClientCommandsEvent;
import net.neoforged.neoforge.common.NeoForge;
import org.slf4j.Logger;

import java.io.IOException;
import java.io.Reader;
import java.io.Writer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Locale;
import java.util.Properties;
import java.util.stream.Stream;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

@Mod(value = ExporterMod.MOD_ID, dist = Dist.CLIENT)
public final class ExporterMod {
    static final String MOD_ID = "mcedit_exporter";

    private static final Logger LOGGER = LogUtils.getLogger();
    private static final String AUTO_EXPORT_PROPERTY = "mcedit.exporter.autoExport";

    private static final String INSTALL_FOLDER_SETTING = "blockPacksFolder";

    private boolean autoExportPending = System.getProperty(AUTO_EXPORT_PROPERTY) != null;
    private Path lastExportFolder;
    private final TestWorldGenerator testWorldGenerator = System.getProperty(TestWorldGenerator.PROPERTY) == null
            ? null : new TestWorldGenerator(System.getProperty(TestWorldGenerator.PROPERTY));
    private final WorldChecker worldChecker = System.getProperty(WorldChecker.PROPERTY) == null
            ? null : new WorldChecker(System.getProperty(WorldChecker.PROPERTY));

    public ExporterMod(IEventBus modBus) {
        NeoForge.EVENT_BUS.addListener(this::registerCommands);
        NeoForge.EVENT_BUS.addListener(this::onClientTick);
    }

    private void registerCommands(RegisterClientCommandsEvent event) {
        CommandDispatcher<CommandSourceStack> dispatcher = event.getDispatcher();
        dispatcher.register(Commands.literal("mcedit_export")
                .executes(context -> runCommand(context, BlockDumper.Scope.MODS))
                .then(Commands.literal("vanilla").executes(context -> runCommand(context, BlockDumper.Scope.VANILLA)))
                .then(Commands.literal("all").executes(context -> runCommand(context, BlockDumper.Scope.ALL)))
                .then(Commands.literal("install")
                        .executes(context -> runInstall(context, null))
                        .then(Commands.argument("folder", StringArgumentType.greedyString())
                                .executes(context -> runInstall(context, StringArgumentType.getString(context, "folder"))))));
    }

    /**
     * Export blocks from mods and put a copy in MCEdit's blockpacks folder, which is remembered once given.
     */
    private int runInstall(CommandContext<CommandSourceStack> context, String folderArgument) {
        CommandSourceStack source = context.getSource();
        try {
            String folder = folderArgument != null ? folderArgument : rememberedInstallFolder();
            if (folder == null) {
                source.sendFailure(Component.literal("Give the path of MCEdit's blockpacks folder once: /mcedit_export install <folder>"));
                return 0;
            }
            int blocks = runCommand(context, BlockDumper.Scope.MODS);
            Path destination = Path.of(folder).resolve("export_" + timestamp() + ".zip");
            zipPack(lastExportFolder, destination);
            rememberInstallFolder(folder);
            source.sendSuccess(() -> Component.literal("Installed the block pack to " + destination), false);
            return blocks;
        } catch (IOException | RuntimeException e) {
            LOGGER.error("Install failed", e);
            source.sendFailure(Component.literal("Install failed: " + e));
            return 0;
        }
    }

    /**
     * A pack holds thousands of small files, which MCEdit reads several times faster from a zip. It is written under
     * another name and then renamed, because MCEdit could otherwise find a pack that is only partly written.
     */
    private static void zipPack(Path from, Path to) throws IOException {
        Files.createDirectories(to.getParent());
        Path partial = to.resolveSibling(to.getFileName() + ".partial");
        List<Path> files;
        try (Stream<Path> walk = Files.walk(from)) {
            files = walk.filter(Files::isRegularFile).toList();
        }
        try (ZipOutputStream zip = new ZipOutputStream(Files.newOutputStream(partial))) {
            for (Path file : files) {
                zip.putNextEntry(new ZipEntry(from.relativize(file).toString().replace('\\', '/')));
                Files.copy(file, zip);
                zip.closeEntry();
            }
        }
        Files.move(partial, to);
    }

    private static Path settingsFile() {
        return Minecraft.getInstance().gameDirectory.toPath().resolve("config").resolve("mcedit_exporter.properties");
    }

    private static String rememberedInstallFolder() throws IOException {
        Path file = settingsFile();
        if (!Files.exists(file)) {
            return null;
        }
        Properties properties = new Properties();
        try (Reader reader = Files.newBufferedReader(file)) {
            properties.load(reader);
        }
        return properties.getProperty(INSTALL_FOLDER_SETTING);
    }

    private static void rememberInstallFolder(String folder) throws IOException {
        Path file = settingsFile();
        Files.createDirectories(file.getParent());
        Properties properties = new Properties();
        properties.setProperty(INSTALL_FOLDER_SETTING, folder);
        try (Writer writer = Files.newBufferedWriter(file)) {
            properties.store(writer, "MCEdit exporter");
        }
    }

    private int runCommand(CommandContext<CommandSourceStack> context, BlockDumper.Scope scope) {
        CommandSourceStack source = context.getSource();
        Path folder = newExportFolder();
        lastExportFolder = folder;
        try {
            BlockDumper.Result result = new BlockDumper().export(folder, scope);
            String message = String.format("Exported %d blocks and %d states to %s", result.blocks(), result.states(), folder);
            if (result.problems() > 0) {
                message += String.format(" (%d problems, see report.json)", result.problems());
            }
            String finalMessage = message;
            source.sendSuccess(() -> Component.literal(finalMessage), false);
            return result.blocks();
        } catch (IOException | RuntimeException e) {
            LOGGER.error("Export failed", e);
            source.sendFailure(Component.literal("Export failed: " + e));
            return 0;
        }
    }

    /**
     * Exports once when asked to by a system property, then exits, so a block table can be generated
     * without anyone at the keyboard. Waits for the loading overlay to close, because models are not
     * baked until then. The first screen is not always the title screen: a first launch shows the
     * accessibility onboarding screen, which waits for input.
     */
    private void onClientTick(ClientTickEvent.Post event) {
        if (testWorldGenerator != null) {
            testWorldGenerator.tick(Minecraft.getInstance());
        }
        if (worldChecker != null) {
            worldChecker.tick(Minecraft.getInstance());
        }
        if (!autoExportPending) {
            return;
        }
        Minecraft minecraft = Minecraft.getInstance();
        if (minecraft.getOverlay() != null || minecraft.screen == null) {
            return;
        }
        autoExportPending = false;

        try {
            String scopeName = System.getProperty(AUTO_EXPORT_PROPERTY).toUpperCase(Locale.ROOT);
            Path folder = newExportFolder();
            BlockDumper.Result result = new BlockDumper().export(folder, BlockDumper.Scope.valueOf(scopeName));
            LOGGER.info("Exported {} blocks and {} states to {} ({} problems)",
                    result.blocks(), result.states(), folder.toAbsolutePath(), result.problems());
        } catch (IOException | RuntimeException e) {
            LOGGER.error("Automatic export failed", e);
        }
        minecraft.stop();
    }

    private static String timestamp() {
        return LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd_HH-mm-ss"));
    }

    private static Path newExportFolder() {
        return Minecraft.getInstance().gameDirectory.toPath().resolve("mcedit_export").resolve(timestamp());
    }
}
