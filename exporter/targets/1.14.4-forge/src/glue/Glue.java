package @PACKAGE@;

import net.mcedit.exporter.export.Exporter;
import net.mcedit.exporter.export.Scope;
import net.mcedit.exporter.export.Session;
import net.minecraft.client.Minecraft;
import net.minecraft.util.text.StringTextComponent;
import net.minecraftforge.client.event.ClientChatEvent;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.fml.ModList;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

import java.util.ArrayList;
import java.util.List;

/**
 * The exporter in Forge for Minecraft 1.14.4. Started by the boot class when the game is this version.
 *
 * Forge for 1.14.4 has no client commands, so the command is taken from the chat box before it is sent.
 */
public final class Glue extends VersionGame {
    private static final Logger LOGGER = LogManager.getLogger("mcedit_exporter");

    private final Session session = new Session(this, Minecraft.getInstance().gameDirectory.toPath(), LOGGER::info);

    public static void start() {
        Glue glue = new Glue();
        MinecraftForge.EVENT_BUS.addListener(glue::onChat);
        MinecraftForge.EVENT_BUS.addListener(glue::onClientTick);
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
        Minecraft.getInstance().gui.getChat().addMessage(new StringTextComponent(text));
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
