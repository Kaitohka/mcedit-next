package @PACKAGE@;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.StringArgumentType;
import net.fabricmc.fabric.api.event.client.ClientTickCallback;
import net.fabricmc.fabric.api.registry.CommandRegistry;
import net.fabricmc.loader.api.FabricLoader;
import net.mcedit.exporter.export.Exporter;
import net.mcedit.exporter.export.Scope;
import net.mcedit.exporter.export.Session;
import net.minecraft.ChatFormatting;
import net.minecraft.client.Minecraft;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.TextComponent;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

import java.util.ArrayList;
import java.util.List;

/**
 * The exporter in Fabric for Minecraft 1.15.2. Started by the boot class when the game is this version.
 *
 * Fabric API has no commands of the client's own for this version, so the command is the game's, and is there in a
 * world the client runs itself: single player, or one opened to the network. It runs on the server's thread, and
 * hands the export to the client's.
 */
public final class Glue extends VersionGame {
    private static final Logger LOGGER = LogManager.getLogger("mcedit_exporter");

    private Session session;

    /** Made when first needed, once the game is there. */
    private Session session() {
        if (session == null) {
            session = new Session(this, Minecraft.getInstance().gameDirectory.toPath(), LOGGER::info);
        }
        return session;
    }

    public static void start() {
        Glue glue = new Glue();
        // Fabric API's first command and tick events, which its builds for every release of 1.14 and 1.15 have.
        CommandRegistry.INSTANCE.register(false, glue::registerCommands);
        ClientTickCallback.EVENT.register(glue::onClientTick);
    }

    @Override
    public String loader() {
        return "fabric";
    }

    @Override
    public String loaderVersion() {
        return FabricLoader.getInstance().getModContainer("fabricloader")
                .map(container -> container.getMetadata().getVersion().getFriendlyString()).orElse(null);
    }

    @Override
    public List<String[]> mods() {
        List<String[]> mods = new ArrayList<>();
        FabricLoader.getInstance().getAllMods().forEach(mod -> mods.add(new String[]{
                mod.getMetadata().getId(), mod.getMetadata().getVersion().getFriendlyString()}));
        return mods;
    }

    private void registerCommands(CommandDispatcher<CommandSourceStack> dispatcher) {
        dispatcher.register(Commands.literal(Session.COMMAND)
                .executes(context -> export(Scope.MODS))
                .then(Commands.literal("vanilla").executes(context -> export(Scope.VANILLA)))
                .then(Commands.literal("all").executes(context -> export(Scope.ALL)))
                .then(Commands.literal("install")
                        .executes(context -> install(null))
                        .then(Commands.argument("folder", StringArgumentType.greedyString())
                                .executes(context -> install(StringArgumentType.getString(context, "folder"))))));
    }

    private int export(Scope scope) {
        onClientThread(() -> {
            try {
                Exporter.Result result = session().export(scope);
                tell(result.message(), false);
            } catch (Exception e) {
                LOGGER.error("Export failed", e);
                tell("Export failed: " + e, true);
            }
        });
        return 1;
    }

    private int install(String folder) {
        onClientThread(() -> {
            try {
                String message = session().install(folder);
                tell(message != null ? message : Session.noInstallFolderMessage(), message == null);
            } catch (Exception e) {
                LOGGER.error("Install failed", e);
                tell("Install failed: " + e, true);
            }
        });
        return 1;
    }

    private static void onClientThread(Runnable task) {
        Minecraft.getInstance().execute(task);
    }

    private static void tell(String message, boolean failed) {
        Minecraft minecraft = Minecraft.getInstance();
        if (minecraft.player != null) {
            TextComponent text = new TextComponent(message);
            if (failed) {
                text.withStyle(ChatFormatting.RED);
            }
            minecraft.gui.getChat().addMessage(text);
        }
    }

    /** Exports once when asked to by a system property, then quits, once the loading overlay has closed. */
    private void onClientTick(Minecraft minecraft) {
        if (!session().autoExportPending() || minecraft.getOverlay() != null
                || (session().exportInWorld() ? minecraft.level == null || minecraft.screen != null : minecraft.screen == null)) {
            return;
        }
        session().runAutoExport();
        minecraft.stop();
    }
}
