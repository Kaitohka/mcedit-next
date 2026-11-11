package net.mcedit.exporter.boot;

import net.neoforged.api.distmarker.Dist;
import net.neoforged.fml.common.Mod;

/** The entry for NeoForge from 1.20.2 on, found by its annotation through META-INF/neoforge.mods.toml. */
@Mod(value = "mcedit_exporter", dist = Dist.CLIENT)
public final class NeoForgeBoot {
    public NeoForgeBoot() {
        Boot.start("neoforge", minecraftVersion());
    }

    private static String minecraftVersion() {
        Object versionInfo = Boot.callStatic("net.neoforged.fml.loading.FMLLoader", "versionInfo");
        Object mcVersion = Boot.call(versionInfo, "mcVersion");
        if (mcVersion != null) {
            return mcVersion.toString();
        }
        try {
            return Boot.versionFromVersionJson(Class.forName("net.minecraft.client.Minecraft", false,
                    NeoForgeBoot.class.getClassLoader()));
        } catch (ClassNotFoundException e) {
            return null;
        }
    }
}
