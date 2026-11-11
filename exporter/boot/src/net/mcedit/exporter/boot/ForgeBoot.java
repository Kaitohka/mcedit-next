package net.mcedit.exporter.boot;

import net.minecraftforge.fml.common.Mod;

/**
 * The entry for Forge from 1.16.5 to 1.20.1, for NeoForge 1.20.1, which is Forge's 1.20.1 under another name, and for
 * Forge 1.12.2. Forge finds it by its annotation: from 1.13 through META-INF/mods.toml, by "value", and before that
 * by "modid", with mcmod.info.
 */
@Mod(value = "mcedit_exporter", modid = "mcedit_exporter", name = "MCEdit Exporter", version = Boot.VERSION,
        clientSideOnly = true)
public final class ForgeBoot {
    public ForgeBoot() {
        Object dist = Boot.callStatic("net.minecraftforge.fml.loading.FMLEnvironment", "getDist");
        if (dist == null) {
            try {
                dist = Class.forName("net.minecraftforge.fml.loading.FMLEnvironment").getField("dist").get(null);
            } catch (Exception | LinkageError e) {
                dist = null;
            }
        }
        if (dist == null) {
            // Before 1.13
            dist = Boot.call(Boot.callStatic("net.minecraftforge.fml.common.FMLCommonHandler", "instance"), "getSide");
        }
        if (dist != null && !"CLIENT".equals(String.valueOf(dist))) {
            Boot.log("The exporter is for the game's client, and does nothing on a server.");
            return;
        }
        Boot.start("forge", minecraftVersion());
    }

    private static String minecraftVersion() {
        Object version = Boot.callStatic("net.minecraftforge.versions.mcp.MCPVersion", "getMCVersion");
        if (version != null) {
            return version.toString();
        }
        try {
            // Before 1.13
            return String.valueOf(Class.forName("net.minecraftforge.fml.common.Loader").getField("MC_VERSION").get(null));
        } catch (Exception | LinkageError e) {
            // From 1.13
        }
        Object versionInfo = Boot.callStatic("net.minecraftforge.fml.loading.FMLLoader", "versionInfo");
        Object mcVersion = Boot.call(versionInfo, "mcVersion");
        if (mcVersion != null) {
            return mcVersion.toString();
        }
        try {
            return Boot.versionFromVersionJson(Class.forName("net.minecraft.client.Minecraft", false,
                    ForgeBoot.class.getClassLoader()));
        } catch (ClassNotFoundException e) {
            return null;
        }
    }
}
