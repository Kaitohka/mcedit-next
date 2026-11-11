package @PACKAGE@;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.context.CommandContext;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandManager;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback;
import net.fabricmc.fabric.api.client.command.v2.FabricClientCommandSource;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.mcedit.exporter.export.Exporter;
import net.mcedit.exporter.export.Scope;
import net.mcedit.exporter.export.Session;
import net.minecraft.client.Minecraft;
import net.minecraft.network.chat.Component;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayList;
import java.util.List;

/** The exporter in Fabric for Minecraft 1.20.4. Started by the boot class when the game is this version. */
public final class Glue extends VersionGame {
    private static final Logger LOGGER = LoggerFactory.getLogger("mcedit_exporter");

    private final Session session = new Session(this, Minecraft.getInstance().gameDirectory.toPath(), LOGGER::info);

    public static void start() {
        Glue glue = new Glue();
        ClientCommandRegistrationCallback.EVENT.register((dispatcher, registryAccess) -> glue.registerCommands(dispatcher));
        ClientTickEvents.END_CLIENT_TICK.register(glue::onClientTick);
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

    private void registerCommands(CommandDispatcher<FabricClientCommandSource> dispatcher) {
        dispatcher.register(ClientCommandManager.literal(Session.COMMAND)
                .executes(context -> export(context, Scope.MODS))
                .then(ClientCommandManager.literal("vanilla").executes(context -> export(context, Scope.VANILLA)))
                .then(ClientCommandManager.literal("all").executes(context -> export(context, Scope.ALL)))
                .then(ClientCommandManager.literal("install")
                        .executes(context -> install(context, null))
                        .then(ClientCommandManager.argument("folder", StringArgumentType.greedyString())
                                .executes(context -> install(context, StringArgumentType.getString(context, "folder"))))));
    }

    private int export(CommandContext<FabricClientCommandSource> context, Scope scope) {
        try {
            Exporter.Result result = session.export(scope);
            context.getSource().sendFeedback(Component.literal(result.message()));
            return result.blocks;
        } catch (Exception e) {
            LOGGER.error("Export failed", e);
            context.getSource().sendError(Component.literal("Export failed: " + e));
            return 0;
        }
    }

    private int install(CommandContext<FabricClientCommandSource> context, String folder) {
        try {
            String message = session.install(folder);
            if (message == null) {
                context.getSource().sendError(Component.literal(Session.noInstallFolderMessage()));
                return 0;
            }
            context.getSource().sendFeedback(Component.literal(message));
            return 1;
        } catch (Exception e) {
            LOGGER.error("Install failed", e);
            context.getSource().sendError(Component.literal("Install failed: " + e));
            return 0;
        }
    }

    /** Exports once when asked to by a system property, then quits, once the loading overlay has closed. */
    private void onClientTick(Minecraft minecraft) {
        if (!session.autoExportPending() || minecraft.getOverlay() != null
                || (session.exportInWorld() ? minecraft.level == null || minecraft.screen != null : minecraft.screen == null)) {
            return;
        }
        session.runAutoExport();
        minecraft.stop();
    }
}
