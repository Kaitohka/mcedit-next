package @PACKAGE@;

import cpw.mods.fml.common.FMLCommonHandler;
import cpw.mods.fml.common.Loader;
import cpw.mods.fml.common.ModContainer;
import cpw.mods.fml.common.eventhandler.SubscribeEvent;
import cpw.mods.fml.common.gameevent.TickEvent;
import net.mcedit.exporter.export.Exporter;
import net.mcedit.exporter.export.Scope;
import net.mcedit.exporter.export.Session;
import net.minecraft.client.Minecraft;
import net.minecraft.command.CommandBase;
import net.minecraft.command.ICommandSender;
import net.minecraft.util.ChatComponentText;
import net.minecraftforge.client.ClientCommandHandler;
import net.minecraftforge.common.ForgeVersion;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

import java.util.ArrayList;
import java.util.List;

/** The exporter in Forge for Minecraft 1.7.10. Started by the boot class when the game is this version. */
public final class Glue extends VersionGame {
    private static final Logger LOGGER = LogManager.getLogger("mcedit_exporter");

    private final Session session = new Session(this, Minecraft.getMinecraft().mcDataDir.toPath(), LOGGER::info);

    public static void start() {
        Glue glue = new Glue();
        ClientCommandHandler.instance.registerCommand(glue.new ExportCommand());
        FMLCommonHandler.instance().bus().register(glue);
    }

    @Override
    public String loader() {
        return "forge";
    }

    @Override
    public String loaderVersion() {
        return ForgeVersion.getVersion();
    }

    @Override
    public List<String[]> mods() {
        List<String[]> mods = new ArrayList<>();
        for (ModContainer mod : Loader.instance().getModList()) {
            mods.add(new String[]{mod.getModId(), mod.getVersion()});
        }
        return mods;
    }

    private final class ExportCommand extends CommandBase {
        @Override
        public String getCommandName() {
            return Session.COMMAND;
        }

        @Override
        public String getCommandUsage(ICommandSender sender) {
            return "/" + Session.COMMAND + " [vanilla|all|install [folder]]";
        }

        @Override
        public int getRequiredPermissionLevel() {
            return 0;
        }

        @Override
        public boolean canCommandSenderUseCommand(ICommandSender sender) {
            return true;
        }

        @Override
        public void processCommand(ICommandSender sender, String[] args) {
            StringBuilder joined = new StringBuilder();
            for (String arg : args) {
                joined.append(joined.length() > 0 ? " " : "").append(arg);
            }
            String argument = joined.toString().trim();
            try {
                if (argument.equals("install") || argument.startsWith("install ")) {
                    String folder = argument.substring("install".length()).trim();
                    String result = session.install(folder.isEmpty() ? null : folder);
                    sender.addChatMessage(new ChatComponentText(result != null ? result : Session.noInstallFolderMessage()));
                } else {
                    Exporter.Result result = session.export(argument.isEmpty() ? Scope.MODS : Scope.parse(argument));
                    sender.addChatMessage(new ChatComponentText(result.message()));
                }
            } catch (IllegalArgumentException e) {
                sender.addChatMessage(new ChatComponentText(getCommandUsage(sender)));
            } catch (Exception e) {
                LOGGER.error("Export failed", e);
                sender.addChatMessage(new ChatComponentText("Export failed: " + e));
            }
        }
    }

    /** Exports once when asked to by a system property, then quits, once the main menu is up. */
    @SubscribeEvent
    public void onClientTick(TickEvent.ClientTickEvent event) {
        if (event.phase != TickEvent.Phase.END || !session.autoExportPending()) {
            return;
        }
        Minecraft minecraft = Minecraft.getMinecraft();
        if (minecraft.currentScreen == null) {
            return;
        }
        session.runAutoExport();
        minecraft.shutdown();
    }
}
