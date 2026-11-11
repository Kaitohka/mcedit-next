package net.mcedit.exporter.boot;

import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.Reader;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.Properties;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Starts the exporter for the version of the game and mod loader it finds itself in. The one jar holds a build of
 * the exporter for each version and loader it supports, each in a package of its own, such as
 * net.mcedit.exporter.v1_20_1_forge. Only the one for this game is ever loaded. The rest are never touched, so it
 * does not matter that they name classes this game doesn't have.
 *
 * This and the loaders' entry classes are compiled for Java 8 and use nothing from the game, since they are loaded
 * before anything is known about it.
 */
public final class Boot {
    /** The exporter's version. build_universal.py reads it from here. */
    public static final String VERSION = "0.2.0";

    /** Which build serves each version, as "<loader>.<version>=<package>", written by build_universal.py. */
    private static final String TARGETS = "/net/mcedit/exporter/boot/targets.properties";

    private static final Pattern VERSION_ID = Pattern.compile("\"id\"\\s*:\\s*\"([^\"]+)\"");

    private Boot() {
    }

    /** Start the build of the exporter for this loader and version, if the jar has one. */
    public static void start(String loader, String minecraftVersion) {
        if (minecraftVersion == null) {
            log("Could not tell which version of Minecraft this is, so the exporter is not started.");
            return;
        }
        String className = "net.mcedit.exporter." + targetFor(loader, minecraftVersion) + ".Glue";
        Class<?> glue;
        try {
            glue = Class.forName(className, true, Boot.class.getClassLoader());
        } catch (ClassNotFoundException e) {
            log("This jar has no exporter for Minecraft " + minecraftVersion + " with " + loader + ", so it does nothing.");
            return;
        }
        try {
            glue.getMethod("start").invoke(null);
            log("Started for Minecraft " + minecraftVersion + " with " + loader + ". Use /mcedit_export.");
        } catch (ReflectiveOperationException | LinkageError e) {
            log("Could not start for Minecraft " + minecraftVersion + " with " + loader + ": " + e);
            Throwable cause = e.getCause() != null ? e.getCause() : e;
            cause.printStackTrace();
        }
    }

    /**
     * The package of the build that serves this version with this loader, such as "v1_21_3_fabric" for 1.21.2,
     * from the table build_universal.py writes. One build often serves several versions whose code is the same.
     */
    static String targetFor(String loader, String minecraftVersion) {
        Properties targets = new Properties();
        try (InputStream stream = Boot.class.getResourceAsStream(TARGETS)) {
            if (stream != null) {
                targets.load(stream);
            }
        } catch (IOException e) {
            log("Could not read " + TARGETS + ": " + e);
        }
        String target = targets.getProperty(loader + "." + minecraftVersion);
        return target != null ? target : "v" + minecraftVersion.replace('.', '_') + "_" + loader;
    }

    /** The version of the game from the version.json in its jar, which every version from 1.14 has. */
    public static String versionFromVersionJson(Class<?> gameClass) {
        try (InputStream stream = gameClass.getResourceAsStream("/version.json")) {
            if (stream == null) {
                return null;
            }
            StringBuilder text = new StringBuilder();
            try (Reader reader = new InputStreamReader(stream, StandardCharsets.UTF_8)) {
                char[] buffer = new char[4096];
                int read;
                while ((read = reader.read(buffer)) > 0) {
                    text.append(buffer, 0, read);
                }
            }
            Matcher matcher = VERSION_ID.matcher(text);
            return matcher.find() ? matcher.group(1) : null;
        } catch (Exception e) {
            return null;
        }
    }

    /** Call a static method found by name, returning null if there is no such method or it fails. */
    public static Object callStatic(String className, String method) {
        try {
            Method found = Class.forName(className, false, Boot.class.getClassLoader()).getMethod(method);
            return found.invoke(null);
        } catch (Exception | LinkageError e) {
            return null;
        }
    }

    /**
     * Call a method with no arguments on an object, returning null if there is no such method or it fails. Loaders
     * often hand out objects of classes that are not public, through public interfaces, and a method found on such
     * a class can't be called as it is, so the interfaces are looked at first.
     */
    public static Object call(Object object, String method) {
        if (object == null) {
            return null;
        }
        for (Class<?> type = object.getClass(); type != null; type = type.getSuperclass()) {
            for (Class<?> face : type.getInterfaces()) {
                Object result = tryCall(face, object, method);
                if (result != null) {
                    return result;
                }
            }
        }
        try {
            Method found = object.getClass().getMethod(method);
            found.setAccessible(true);
            return found.invoke(object);
        } catch (Exception | LinkageError e) {
            return null;
        }
    }

    private static Object tryCall(Class<?> type, Object object, String method) {
        try {
            return type.getMethod(method).invoke(object);
        } catch (Exception | LinkageError e) {
            return null;
        }
    }

    static void log(String message) {
        System.out.println("[MCEdit Exporter] " + message);
    }
}
