package @PACKAGE@;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.context.CommandContext;
import net.fabricmc.fabric.api.client.command.v1.ClientCommandManager;
import net.fabricmc.fabric.api.client.command.v1.FabricClientCommandSource;
import net.fabricmc.fabric.api.event.client.ClientTickCallback;
import net.fabricmc.fabric.api.command.v1.CommandRegistrationCallback;
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
 * The exporter in Fabric for Minecraft 1.16.5, and 1.16 to 1.16.4. Started by the boot class when the game is one of
 * these versions.
 *
 * Fabric API has commands of the client's own from its builds for 1.16.5. Before those, the command is the game's,
 * and is there in a world the client runs itself: single player, or one opened to the network. It runs on the
 * server's thread, and hands the export to the client's.
 */
public final class Glue extends VersionGame {
    private static final Logger LOGGER = LogManager.getLogger("mcedit_exporter");
    private static final String CLIENT_COMMANDS = "net.fabricmc.fabric.api.client.command.v1.ClientCommandManager";

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
        if (hasClass(CLIENT_COMMANDS)) {
            ClientCommands.register(glue);
        } else {
            CommandRegistrationCallback.EVENT.register((dispatcher, dedicated) -> {
                if (!dedicated) {
                    glue.registerServerCommands(dispatcher);
                }
            });
        }
        // Fabric API's first tick event, which its builds for 1.16 have, unlike the one that replaced it.
        ClientTickCallback.EVENT.register(glue::onClientTick);
    }

    private static boolean hasClass(String name) {
        try {
            Class.forName(name, false, Glue.class.getClassLoader());
            return true;
        } catch (ClassNotFoundException | LinkageError e) {
            return false;
        }
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

    /** The command as one of the client's own. Only loaded when Fabric API has those. */
    private static final class ClientCommands {
        static void register(Glue glue) {
            CommandDispatcher<FabricClientCommandSource> dispatcher = ClientCommandManager.DISPATCHER;
            dispatcher.register(ClientCommandManager.literal(Session.COMMAND)
                    .executes(context -> export(glue, context, Scope.MODS))
                    .then(ClientCommandManager.literal("vanilla").executes(context -> export(glue, context, Scope.VANILLA)))
                    .then(ClientCommandManager.literal("all").executes(context -> export(glue, context, Scope.ALL)))
                    .then(ClientCommandManager.literal("install")
                            .executes(context -> install(glue, context, null))
                            .then(ClientCommandManager.argument("folder", StringArgumentType.greedyString())
                                    .executes(context -> install(glue, context,
                                            StringArgumentType.getString(context, "folder"))))));
        }

        private static int export(Glue glue, CommandContext<FabricClientCommandSource> context, Scope scope) {
            try {
                Exporter.Result result = glue.session().export(scope);
                context.getSource().sendFeedback(new TextComponent(result.message()));
                return result.blocks;
            } catch (Exception e) {
                LOGGER.error("Export failed", e);
                context.getSource().sendError(new TextComponent("Export failed: " + e));
                return 0;
            }
        }

        private static int install(Glue glue, CommandContext<FabricClientCommandSource> context, String folder) {
            try {
                String message = glue.session().install(folder);
                if (message == null) {
                    context.getSource().sendError(new TextComponent(Session.noInstallFolderMessage()));
                    return 0;
                }
                context.getSource().sendFeedback(new TextComponent(message));
                return 1;
            } catch (Exception e) {
                LOGGER.error("Install failed", e);
                context.getSource().sendError(new TextComponent("Install failed: " + e));
                return 0;
            }
        }
    }

    /** The command as the game's, before Fabric API had commands of the client's own. */
    private void registerServerCommands(CommandDispatcher<CommandSourceStack> dispatcher) {
        dispatcher.register(Commands.literal(Session.COMMAND)
                .executes(context -> exportLater(Scope.MODS))
                .then(Commands.literal("vanilla").executes(context -> exportLater(Scope.VANILLA)))
                .then(Commands.literal("all").executes(context -> exportLater(Scope.ALL)))
                .then(Commands.literal("install")
                        .executes(context -> installLater(null))
                        .then(Commands.argument("folder", StringArgumentType.greedyString())
                                .executes(context -> installLater(StringArgumentType.getString(context, "folder"))))));
    }

    private int exportLater(Scope scope) {
        Minecraft.getInstance().execute(() -> {
            try {
                tell(session().export(scope).message(), false);
            } catch (Exception e) {
                LOGGER.error("Export failed", e);
                tell("Export failed: " + e, true);
            }
        });
        return 1;
    }

    private int installLater(String folder) {
        Minecraft.getInstance().execute(() -> {
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
