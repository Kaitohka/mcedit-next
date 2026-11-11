package net.mcedit.exporter.export;

import java.io.IOException;
import java.io.Reader;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.Properties;
import java.util.function.Consumer;

/**
 * What the exporter's commands do, the same in every version of the game. Each loader connects its own commands,
 * tick events and log to this.
 *
 * <pre>
 * /mcedit_export                  export the blocks of mods
 * /mcedit_export vanilla | all    export the game's own blocks, or every block
 * /mcedit_export install [folder] export the blocks of mods into MCEdit's blockpacks folder, remembered once given
 * </pre>
 *
 * Setting the system property mcedit.exporter.autoExport to mods, vanilla or all exports once the game has loaded,
 * and then quits, so that a pack can be made with nobody at the keyboard.
 */
public final class Session {
    public static final String COMMAND = "mcedit_export";
    public static final String AUTO_EXPORT_PROPERTY = "mcedit.exporter.autoExport";
    private static final String INSTALL_FOLDER_SETTING = "blockPacksFolder";

    private final Game game;
    private final Path gameDirectory;
    private final Consumer<String> log;
    private boolean autoExportPending = System.getProperty(AUTO_EXPORT_PROPERTY) != null;

    public Session(Game game, Path gameDirectory, Consumer<String> log) {
        this.game = game;
        this.gameDirectory = gameDirectory;
        this.log = log;
    }

    /** Export, and return the message to show. Throws if the export fails. */
    public Exporter.Result export(Scope scope) throws IOException {
        Path folder = gameDirectory.resolve("mcedit_export").resolve(timestamp());
        Exporter.Result result = new Exporter(game).export(folder, scope);
        log.accept(result.message());
        return result;
    }

    /**
     * Export the blocks of mods and put a zip of them in MCEdit's blockpacks folder, and return the message to show.
     * Returns null if no folder was given now or before.
     */
    public String install(String folderArgument) throws IOException {
        String folder = folderArgument != null ? folderArgument : rememberedInstallFolder();
        if (folder == null) {
            return null;
        }
        Exporter.Result result = export(Scope.MODS);
        Path destination = Paths.get(folder).resolve("export_" + timestamp() + ".zip");
        Exporter.zipPack(result.folder, destination);
        rememberInstallFolder(folder);
        return "Installed the block pack to " + destination;
    }

    public static String noInstallFolderMessage() {
        return "Give the path of MCEdit's blockpacks folder once: /" + COMMAND + " install <folder>";
    }

    /**
     * Whether the export asked for when the game started is to wait for a world, as with the system property
     * mcedit.exporter.inWorld and a world to join at start. Some block entity renderers draw nothing without one.
     */
    public boolean exportInWorld() {
        return System.getProperty("mcedit.exporter.inWorld") != null;
    }

    /** Whether an export was asked for when the game started and has not been done yet. */
    public boolean autoExportPending() {
        return autoExportPending;
    }

    /** Do the export asked for when the game started. The caller quits the game afterwards. */
    public void runAutoExport() {
        autoExportPending = false;
        try {
            Exporter.Result result = export(Scope.parse(System.getProperty(AUTO_EXPORT_PROPERTY)));
            log.accept("Automatic export finished: " + result.folder.toAbsolutePath());
        } catch (IOException | RuntimeException e) {
            log.accept("Automatic export failed: " + e);
        }
    }

    private Path settingsFile() {
        return gameDirectory.resolve("config").resolve("mcedit_exporter.properties");
    }

    private String rememberedInstallFolder() throws IOException {
        Path file = settingsFile();
        if (!Files.exists(file)) {
            return null;
        }
        Properties properties = new Properties();
        try (Reader reader = Files.newBufferedReader(file, StandardCharsets.UTF_8)) {
            properties.load(reader);
        }
        return properties.getProperty(INSTALL_FOLDER_SETTING);
    }

    private void rememberInstallFolder(String folder) throws IOException {
        Path file = settingsFile();
        Files.createDirectories(file.getParent());
        Properties properties = new Properties();
        properties.setProperty(INSTALL_FOLDER_SETTING, folder);
        try (Writer writer = Files.newBufferedWriter(file, StandardCharsets.UTF_8)) {
            properties.store(writer, "MCEdit exporter");
        }
    }

    private static String timestamp() {
        return LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd_HH-mm-ss"));
    }
}
