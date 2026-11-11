package @PACKAGE@;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.context.CommandContext;
import net.mcedit.exporter.export.Exporter;
import net.mcedit.exporter.export.Scope;
import net.mcedit.exporter.export.Session;
import net.minecraft.client.Minecraft;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import net.neoforged.fml.ModList;
import net.neoforged.neoforge.client.event.ClientTickEvent;
import net.neoforged.neoforge.client.event.RegisterClientCommandsEvent;
import net.neoforged.neoforge.common.NeoForge;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayList;
import java.util.List;

/** The exporter in NeoForge for Minecraft 1.21.11. Started by the boot class when the game is this version. */
public final class Glue extends VersionGame {
    private static final Logger LOGGER = LoggerFactory.getLogger("mcedit_exporter");

    private Session session;

    /** Made when first needed: from 1.21.5, NeoForge constructs mods before the game exists. */
    private Session session() {
        if (session == null) {
            session = new Session(this, Minecraft.getInstance().gameDirectory.toPath(), LOGGER::info);
        }
        return session;
    }

    public static void start() {
        Glue glue = new Glue();
        NeoForge.EVENT_BUS.addListener(glue::registerCommands);
        NeoForge.EVENT_BUS.addListener(glue::onClientTick);
    }

    @Override
    public String loader() {
        return "neoforge";
    }

    @Override
    public String loaderVersion() {
        return ModList.get().getModContainerById("neoforge")
                .map(container -> container.getModInfo().getVersion().toString()).orElse(null);
    }

    @Override
    public List<String[]> mods() {
        List<String[]> mods = new ArrayList<>();
        ModList.get().getMods().forEach(mod -> mods.add(new String[]{mod.getModId(), mod.getVersion().toString()}));
        return mods;
    }

    private void registerCommands(RegisterClientCommandsEvent event) {
        CommandDispatcher<CommandSourceStack> dispatcher = event.getDispatcher();
        dispatcher.register(Commands.literal(Session.COMMAND)
                .executes(context -> export(context, Scope.MODS))
                .then(Commands.literal("vanilla").executes(context -> export(context, Scope.VANILLA)))
                .then(Commands.literal("all").executes(context -> export(context, Scope.ALL)))
                .then(Commands.literal("install")
                        .executes(context -> install(context, null))
                        .then(Commands.argument("folder", StringArgumentType.greedyString())
                                .executes(context -> install(context, StringArgumentType.getString(context, "folder"))))));
    }

    private int export(CommandContext<CommandSourceStack> context, Scope scope) {
        try {
            Exporter.Result result = session().export(scope);
            context.getSource().sendSuccess(() -> Component.literal(result.message()), false);
            return result.blocks;
        } catch (Exception e) {
            LOGGER.error("Export failed", e);
            context.getSource().sendFailure(Component.literal("Export failed: " + e));
            return 0;
        }
    }

    private int install(CommandContext<CommandSourceStack> context, String folder) {
        try {
            String message = session().install(folder);
            if (message == null) {
                context.getSource().sendFailure(Component.literal(Session.noInstallFolderMessage()));
                return 0;
            }
            context.getSource().sendSuccess(() -> Component.literal(message), false);
            return 1;
        } catch (Exception e) {
            LOGGER.error("Install failed", e);
            context.getSource().sendFailure(Component.literal("Install failed: " + e));
            return 0;
        }
    }

    /** Exports once when asked to by a system property, then quits, once the loading overlay has closed. */
    private void onClientTick(ClientTickEvent.Post event) {
        Minecraft minecraft = Minecraft.getInstance();
        if (!session().autoExportPending() || minecraft.getOverlay() != null
                || (session().exportInWorld() ? minecraft.level == null || minecraft.screen != null : minecraft.screen == null)) {
            return;
        }
        session().runAutoExport();
        minecraft.stop();
    }
}
