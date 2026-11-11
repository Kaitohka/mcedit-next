package net.minecraftforge.fml.common;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Compile-time stand-in for Forge's annotation. Not packaged: Forge has the real one. It has the elements of both
 * the annotation of Forge from 1.13, which reads "value", and that of Forge before, which reads "modid" and the rest.
 * Each ignores the ones it doesn't have, so one class can be the mod for both.
 */
@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.TYPE)
public @interface Mod {
    String value() default "";

    String modid() default "";

    String name() default "";

    String version() default "";

    boolean clientSideOnly() default false;
}
