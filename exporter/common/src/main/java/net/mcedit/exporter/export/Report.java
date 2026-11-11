package net.mcedit.exporter.export;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.function.Supplier;

/**
 * What could not be read from the game. Blocks from mods can throw from any call, since the exporter reads them
 * with no world around them, so each read is guarded and a failure is reported instead of ending the export.
 */
public final class Report {
    private static final int MOST_PROBLEMS = 5000;

    private final Set<String> problems = new LinkedHashSet<>();

    public void add(String problem) {
        if (problems.size() < MOST_PROBLEMS) {
            problems.add(problem);
        }
    }

    /** Read something about the thing called `id`, or give `fallback` and report why it could not be read. */
    public <T> T guarded(String id, String what, Supplier<T> read, T fallback) {
        try {
            return read.get();
        } catch (Exception | LinkageError e) {
            add(id + ": could not read " + what + ": " + e + where(e));
            return fallback;
        }
    }

    /**
     * Where an exception with little or no message of its own came from, as old Java's NullPointerException, which
     * says nothing without it: " at" its first place outside Java itself, or nothing.
     */
    static String where(Throwable e) {
        Throwable cause = e;
        while (cause.getCause() != null && cause.getCause() != cause) {
            cause = cause.getCause();
        }
        if (cause.getMessage() != null && cause.getMessage().length() > 12) {
            return "";  // its message says enough; a short one, as an index's "3", does not
        }
        for (StackTraceElement frame : cause.getStackTrace()) {
            if (!frame.getClassName().startsWith("java.") && !frame.getClassName().startsWith("sun.")
                    && !frame.getClassName().startsWith("com.google.")) {
                return " at " + frame;
            }
        }
        return "";
    }

    public List<String> problems() {
        return new ArrayList<>(problems);
    }

    public int size() {
        return problems.size();
    }
}
