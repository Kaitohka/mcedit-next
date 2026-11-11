package net.mcedit.exporter.boot;

import net.fabricmc.api.ClientModInitializer;

/**
 * The entry for Fabric, and Quilt, which loads Fabric mods. fabric.mod.json names it as a client entrypoint.
 *
 * The exporter needs Fabric API, whose id was "fabric" until 1.18 and is "fabric-api" since, which provided "fabric"
 * as well until 26.1. fabric.mod.json cannot depend on either of two ids, so this checks for it instead.
 */
public final class FabricBoot implements ClientModInitializer {
    @Override
    public void onInitializeClient() {
        if (!isLoaded("fabric-api") && !isLoaded("fabric")) {
            Boot.log("Fabric API is not installed, so the exporter is not started. Add it to the mods folder.");
            return;
        }
        Boot.start("fabric", minecraftVersion());
    }

    /** FabricLoader.getInstance().isModLoaded(id) */
    private static boolean isLoaded(String id) {
        try {
            Object loader = Boot.callStatic("net.fabricmc.loader.api.FabricLoader", "getInstance");
            return Boolean.TRUE.equals(loader.getClass().getMethod("isModLoaded", String.class).invoke(loader, id));
        } catch (Exception | LinkageError e) {
            return true;  // can't tell, so let the glue try
        }
    }

    /** FabricLoader.getInstance().getModContainer("minecraft").get().getMetadata().getVersion().getFriendlyString() */
    private static String minecraftVersion() {
        try {
            Object loader = Boot.callStatic("net.fabricmc.loader.api.FabricLoader", "getInstance");
            Object container = loader.getClass().getMethod("getModContainer", String.class).invoke(loader, "minecraft");
            Object present = Boot.call(container, "get");  // an Optional
            Object version = Boot.call(Boot.call(present, "getMetadata"), "getVersion");
            Object name = Boot.call(version, "getFriendlyString");
            return name != null ? name.toString() : null;
        } catch (Exception | LinkageError e) {
            return null;
        }
    }
}
