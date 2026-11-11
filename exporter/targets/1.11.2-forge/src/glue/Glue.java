package @PACKAGE@;

import net.mcedit.exporter.export.Exporter;
import net.mcedit.exporter.export.Scope;
import net.mcedit.exporter.export.Session;
import net.minecraft.client.Minecraft;
import net.minecraft.command.CommandBase;
import net.minecraft.command.ICommandSender;
import net.minecraft.server.MinecraftServer;
import net.minecraft.util.text.TextComponentString;
import net.minecraftforge.client.ClientCommandHandler;
import net.minecraftforge.common.ForgeVersion;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.fml.common.Loader;
import net.minecraftforge.fml.common.ModContainer;
import net.minecraftforge.fml.common.eventhandler.SubscribeEvent;
import net.minecraftforge.fml.common.gameevent.TickEvent;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

import java.util.ArrayList;
import java.util.List;

/** The exporter in Forge for Minecraft 1.11.2. Started by the boot class when the game is this version. */
public final class Glue extends VersionGame {
    private static final Logger LOGGER = LogManager.getLogger("mcedit_exporter");

    private final Session session = new Session(this, Minecraft.getMinecraft().mcDataDir.toPath(), LOGGER::info);

    public static void start() {
        Glue glue = new Glue();
        ClientCommandHandler.instance.registerCommand(glue.new ExportCommand());
        MinecraftForge.EVENT_BUS.register(glue);
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
        public String getName() {
            return Session.COMMAND;
        }

        @Override
        public String getUsage(ICommandSender sender) {
            return "/" + Session.COMMAND + " [vanilla|all|install [folder]]";
        }

        @Override
        public int getRequiredPermissionLevel() {
            return 0;
        }

        @Override
        public boolean checkPermission(MinecraftServer server, ICommandSender sender) {
            return true;
        }

        @Override
        public void execute(MinecraftServer server, ICommandSender sender, String[] args) {
            String argument = String.join(" ", args).trim();
            try {
                if (argument.equals("install") || argument.startsWith("install ")) {
                    String folder = argument.substring("install".length()).trim();
                    String result = session.install(folder.isEmpty() ? null : folder);
                    sender.sendMessage(new TextComponentString(result != null ? result : Session.noInstallFolderMessage()));
                } else {
                    Exporter.Result result = session.export(argument.isEmpty() ? Scope.MODS : Scope.parse(argument));
                    sender.sendMessage(new TextComponentString(result.message()));
                }
            } catch (IllegalArgumentException e) {
                sender.sendMessage(new TextComponentString(getUsage(sender)));
            } catch (Exception e) {
                LOGGER.error("Export failed", e);
                sender.sendMessage(new TextComponentString("Export failed: " + e));
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
