package net.mcedit.exporter.boot;

import cpw.mods.fml.common.Mod;

/**
 * The entry for Forge 1.7.10, whose annotation is in another package: FML for 1.7.10 does not see ForgeBoot, and
 * later Forge does not see this. Found with mcmod.info.
 */
@Mod(modid = "mcedit_exporter", name = "MCEdit Exporter", version = Boot.VERSION)
public final class LegacyForgeBoot {
    public LegacyForgeBoot() {
        Object side = Boot.call(Boot.callStatic("cpw.mods.fml.common.FMLCommonHandler", "instance"), "getSide");
        if (side != null && !"CLIENT".equals(String.valueOf(side))) {
            Boot.log("The exporter is for the game's client, and does nothing on a server.");
            return;
        }
        String version;
        try {
            version = String.valueOf(Class.forName("cpw.mods.fml.common.Loader").getField("MC_VERSION").get(null));
        } catch (Exception | LinkageError e) {
            version = null;
        }
        Boot.start("forge", version);
    }
}
