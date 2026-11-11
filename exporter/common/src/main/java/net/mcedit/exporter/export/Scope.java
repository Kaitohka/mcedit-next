package net.mcedit.exporter.export;

import java.util.Locale;
import java.util.function.Predicate;

/** Which blocks to export, by the namespace of their names. */
public enum Scope implements Predicate<String> {
    MODS,
    VANILLA,
    ALL;

    @Override
    public boolean test(String name) {
        boolean vanilla = name.startsWith("minecraft:");
        switch (this) {
            case MODS:
                return !vanilla;
            case VANILLA:
                return vanilla;
            default:
                return true;
        }
    }

    public static Scope parse(String name) {
        return valueOf(name.trim().toUpperCase(Locale.ROOT));
    }
}
