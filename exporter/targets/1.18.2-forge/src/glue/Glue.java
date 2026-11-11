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
import net.minecraft.network.chat.TextComponent;
import net.minecraftforge.client.event.ClientChatEvent;
import net.minecraftforge.client.event.RegisterClientCommandsEvent;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.eventbus.api.EventPriority;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.fml.ModList;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayList;
import java.util.List;

/**
 * The exporter in Forge for Minecraft 1.18.2, 1.18.1 and 1.18. Started by the boot class when the game is one of
 * these versions.
 *
 * Forge for 1.18 has no client commands yet, so there the command is taken from the chat box before it is sent.
 */
public final class Glue extends VersionGame {
    private static final Logger LOGGER = LoggerFactory.getLogger("mcedit_exporter");

    private final Session session = new Session(this, Minecraft.getInstance().gameDirectory.toPath(), LOGGER::info);

    public static void start() {
        Glue glue = new Glue();
        if (hasClass("net.minecraftforge.client.event.RegisterClientCommandsEvent")) {
            ClientCommands.listen(glue);
        } else {
            MinecraftForge.EVENT_BUS.addListener(glue::onChat);
        }
        MinecraftForge.EVENT_BUS.addListener(glue::onClientTick);
    }

    private static boolean hasClass(String name) {
        try {
            Class.forName(name, false, Glue.class.getClassLoader());
            return true;
        } catch (ClassNotFoundException | LinkageError e) {
            return false;
        }
    }

    /** Only loaded when Forge has client commands. */
    private static final class ClientCommands {
        static void listen(Glue glue) {
            MinecraftForge.EVENT_BUS.addListener(EventPriority.NORMAL, false, RegisterClientCommandsEvent.class,
                    event -> glue.registerCommands(event.getDispatcher()));
        }
    }

    @Override
    public String loader() {
        return "forge";
    }

    @Override
    public String loaderVersion() {
        return ModList.get().getModContainerById("forge")
                .map(container -> container.getModInfo().getVersion().toString()).orElse(null);
    }

    @Override
    public List<String[]> mods() {
        List<String[]> mods = new ArrayList<>();
        ModList.get().getMods().forEach(mod -> mods.add(new String[]{mod.getModId(), mod.getVersion().toString()}));
        return mods;
    }

    private void registerCommands(CommandDispatcher<CommandSourceStack> dispatcher) {
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
            Exporter.Result result = session.export(scope);
            context.getSource().sendSuccess(new TextComponent(result.message()), false);
            return result.blocks;
        } catch (Exception e) {
            LOGGER.error("Export failed", e);
            context.getSource().sendFailure(new TextComponent("Export failed: " + e));
            return 0;
        }
    }

    private int install(CommandContext<CommandSourceStack> context, String folder) {
        try {
            String message = session.install(folder);
            if (message == null) {
                context.getSource().sendFailure(new TextComponent(Session.noInstallFolderMessage()));
                return 0;
            }
            context.getSource().sendSuccess(new TextComponent(message), false);
            return 1;
        } catch (Exception e) {
            LOGGER.error("Install failed", e);
            context.getSource().sendFailure(new TextComponent("Install failed: " + e));
            return 0;
        }
    }

    private void onChat(ClientChatEvent event) {
        String message = event.getMessage().trim();
        if (!message.equals("/" + Session.COMMAND) && !message.startsWith("/" + Session.COMMAND + " ")) {
            return;
        }
        event.setCanceled(true);
        Minecraft.getInstance().gui.getChat().addRecentChat(message);
        String argument = message.substring(Session.COMMAND.length() + 1).trim();
        try {
            if (argument.equals("install") || argument.startsWith("install ")) {
                String folder = argument.substring("install".length()).trim();
                String result = session.install(folder.isEmpty() ? null : folder);
                say(result != null ? result : Session.noInstallFolderMessage());
            } else {
                Exporter.Result result = session.export(argument.isEmpty() ? Scope.MODS : Scope.parse(argument));
                say(result.message());
            }
        } catch (IllegalArgumentException e) {
            say("Use /" + Session.COMMAND + " [vanilla|all|install [folder]]");
        } catch (Exception e) {
            LOGGER.error("Export failed", e);
            say("Export failed: " + e);
        }
    }

    private static void say(String text) {
        Minecraft.getInstance().gui.getChat().addMessage(new TextComponent(text));
    }

    /**
     * Exports once when asked to by a system property, then quits. Waits for the loading overlay to close, because
     * models are not baked until then.
     */
    private void onClientTick(TickEvent.ClientTickEvent event) {
        if (event.phase != TickEvent.Phase.END || !session.autoExportPending()) {
            return;
        }
        Minecraft minecraft = Minecraft.getInstance();
        if (minecraft.getOverlay() != null
                || (session.exportInWorld() ? minecraft.level == null || minecraft.screen != null : minecraft.screen == null)) {
            return;
        }
        session.runAutoExport();
        minecraft.stop();
    }
}
