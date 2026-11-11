package net.mcedit.exporter.export;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * The faces a block entity renderer draws before 1.15, when renderers draw with OpenGL itself: through display lists,
 * matrices of their own and textures bound as they go. OpenGL's feedback mode gives back every polygon drawn, display
 * lists and all, with its corners where the matrices put them and their texture coordinates and colors, and draws
 * nothing. The renderer is run in it with nothing but an orthographic view of the blocks around it.
 *
 * Feedback says nothing of which texture was bound: a texture manager standing in for the game's tells this class of
 * each texture bound, and it leaves a mark in the feedback there. OpenGL is reached by reflection, as LWJGL 2 and 3
 * have the same class and names for all of this.
 */
public final class FeedbackCapture {
    /** How far from the block's corner, in blocks, faces are kept; further out they are cut off. */
    private static final float RANGE = 4f;
    /** The size of the view in pixels, which is as fine as positions come back. */
    private static final int SIZE = 4096;

    private static final int GL_RENDER = 0x1C00;
    private static final int GL_FEEDBACK = 0x1C01;
    private static final int GL_3D_COLOR_TEXTURE = 0x0603;
    private static final int GL_MODELVIEW = 0x1700;
    private static final int GL_PROJECTION = 0x1701;
    private static final int GL_TEXTURE = 0x1702;
    private static final int GL_ALL_ATTRIB_BITS = 0x000FFFFF;
    private static final int GL_CW = 0x0900;
    private static final int GL_CCW = 0x0901;
    private static final int GL_CULL_FACE = 0x0B44;
    private static final int GL_ACTIVE_TEXTURE = 0x84E0;
    private static final int GL_TEXTURE0 = 0x84C0;

    private static final int PASS_THROUGH = 0x0700;
    private static final int POINT = 0x0701;
    private static final int LINE = 0x0702;
    private static final int POLYGON = 0x0703;
    private static final int BITMAP = 0x0704;
    private static final int DRAW_PIXEL = 0x0705;
    private static final int COPY_PIXEL = 0x0706;
    private static final int LINE_RESET = 0x0707;
    /** x, y, z, then red, green, blue, alpha, then s, t, r, q */
    private static final int VERTEX = 11;

    /** A face as drawn: four corners of x, y, z, u, v in blocks and the texture's own coordinates. */
    public static final class Face {
        public final float[] corners;
        /** The texture bound, as "namespace:textures/....png", or null if none was said to be. */
        public final String texture;
        /** The face's color as 0xAARRGGBB with the light it was drawn in taken out, or -1 for white. */
        public final int color;

        Face(float[] corners, String texture, int color) {
            this.corners = corners;
            this.texture = texture;
            this.color = color;
        }
    }

    /** A sprite of the block atlas: its name, as "namespace:block/stone", and where it is on the atlas. */
    public static final class Sprite {
        final String name;
        final float u0, u1, v0, v1;

        public Sprite(String name, float u0, float u1, float v0, float v1) {
            this.name = name;
            this.u0 = u0;
            this.u1 = u1;
            this.v0 = v0;
            this.v1 = v1;
        }
    }

    private final List<String> textures = new ArrayList<>();
    private boolean capturing;

    /**
     * Faces as quads. A face drawn from an atlas takes the sprite its middle is in, the smallest there as one whose
     * file was missing can have bounds over the whole atlas, with its texture coordinates made relative to it; one
     * drawn with any other texture has it as a file of its own. Faces from no known sprite are left out.
     */
    public static List<Quad> quads(List<Face> faces, List<Sprite> sprites) {
        List<Quad> quads = new ArrayList<>();
        for (Face face : faces) {
            float[] corners = face.corners.clone();
            String texture = face.texture;
            if (texture.contains(":textures/atlas/")) {
                if (!texture.endsWith(":textures/atlas/blocks.png")) {
                    continue;  // the items' atlas of 1.7.10, whose sprites aren't known here
                }
                float u = (corners[3] + corners[8] + corners[13] + corners[18]) / 4;
                float v = (corners[4] + corners[9] + corners[14] + corners[19]) / 4;
                Sprite found = null;
                float area = Float.MAX_VALUE;
                for (Sprite sprite : sprites) {
                    float size = (sprite.u1 - sprite.u0) * (sprite.v1 - sprite.v0);
                    if (u >= sprite.u0 && u <= sprite.u1 && v >= sprite.v0 && v <= sprite.v1 && size < area) {
                        found = sprite;
                        area = size;
                    }
                }
                if (found == null) {
                    continue;
                }
                for (int i = 0; i < 4; i++) {
                    corners[i * 5 + 3] = Quad.relative(corners[i * 5 + 3], found.u0, found.u1);
                    corners[i * 5 + 4] = Quad.relative(corners[i * 5 + 4], found.v0, found.v1);
                }
                texture = found.name.indexOf(':') < 0 ? "minecraft:" + found.name : found.name;
            }
            quads.add(new Quad(corners, texture, Quad.nearestSide(corners), null, -1, true, face.color));
        }
        return quads;
    }

    /** The values of an object's maps that are of a type, as the sprites an atlas keeps in a private map. */
    public static List<Object> mapValues(Object owner, Class<?> type) {
        List<Object> values = new ArrayList<>();
        if (owner == null) {
            return values;
        }
        for (Class<?> c = owner.getClass(); c != null && c != Object.class; c = c.getSuperclass()) {
            for (Field field : c.getDeclaredFields()) {
                if (!Map.class.isAssignableFrom(field.getType()) || Modifier.isStatic(field.getModifiers())) {
                    continue;
                }
                try {
                    field.setAccessible(true);
                    Object map = field.get(owner);
                    if (map == null) {
                        continue;
                    }
                    for (Object value : ((Map<?, ?>) map).values()) {
                        if (type.isInstance(value) && !containsSame(values, value)) {
                            values.add(value);
                        }
                    }
                } catch (ReflectiveOperationException | RuntimeException ignored) {
                    // not a map that can be read
                }
            }
        }
        return values;
    }

    /** For the standing-in texture manager: the renderer has bound a texture. */
    public void bound(String texture) {
        // Only the first texture unit's: the game binds its light map on the second.
        if (!capturing || depth(GL_ACTIVE_TEXTURE) != GL_TEXTURE0) {
            return;
        }
        int index = textures.indexOf(texture);
        if (index < 0) {
            index = textures.size();
            textures.add(texture);
        }
        call("glPassThrough", new Class<?>[]{float.class}, (float) index);
    }

    /**
     * Run a renderer, and give the faces it draws. It is run twice, with the faces turned each way, so that faces
     * the renderer's own culling would leave out show once one way or the other.
     *
     * @param texture the texture bound before the renderer starts, as the game has the block atlas bound
     */
    public List<Face> capture(String texture, Runnable draw) {
        Map<String, Face> faces = new LinkedHashMap<>();
        for (int winding : new int[]{GL_CCW, GL_CW}) {
            for (Face face : pass(texture, draw, winding)) {
                faces.putIfAbsent(key(face), face);
            }
        }
        return new ArrayList<>(faces.values());
    }

    private List<Face> pass(String texture, Runnable draw, int winding) {
        int size = 1 << 18;
        while (true) {
            FloatBuffer buffer = ByteBuffer.allocateDirect(size * 4).order(ByteOrder.nativeOrder()).asFloatBuffer();
            int count = run(buffer, texture, draw, winding);
            if (count >= 0) {
                float[] values = new float[count];
                buffer.position(0);
                buffer.get(values, 0, count);
                return parse(values, texture);
            }
            if (size >= 1 << 24) {
                return new ArrayList<>();  // more than any one block draws
            }
            size <<= 2;
        }
    }

    /** One pass in feedback mode. Returns how many numbers came back, or -1 if they didn't fit. */
    private int run(FloatBuffer buffer, String texture, Runnable draw, int winding) {
        int[] depths = new int[STACK_DEPTHS.length];
        for (int i = 0; i < depths.length; i++) {
            depths[i] = depth(STACK_DEPTHS[i]);
        }
        call("glFeedbackBuffer", new Class<?>[]{int.class, FloatBuffer.class}, GL_3D_COLOR_TEXTURE, buffer);
        call("glPushAttrib", new Class<?>[]{int.class}, GL_ALL_ATTRIB_BITS);
        matrix(GL_TEXTURE, true);
        matrix(GL_PROJECTION, true);
        call("glOrtho", new Class<?>[]{double.class, double.class, double.class, double.class, double.class,
                double.class}, (double) -RANGE, (double) RANGE, (double) -RANGE, (double) RANGE, (double) -RANGE,
                (double) RANGE);
        matrix(GL_MODELVIEW, true);
        call("glViewport", new Class<?>[]{int.class, int.class, int.class, int.class}, 0, 0, SIZE, SIZE);
        call("glFrontFace", new Class<?>[]{int.class}, winding);
        if (winding == GL_CCW) {
            call("glDisable", new Class<?>[]{int.class}, GL_CULL_FACE);
        }
        call("glRenderMode", new Class<?>[]{int.class}, GL_FEEDBACK);
        textures.clear();
        capturing = true;
        int count;
        try {
            draw.run();
        } finally {
            capturing = false;
            count = (Integer) call("glRenderMode", new Class<?>[]{int.class}, GL_RENDER);
            // Back to the depths before, which a renderer that failed partway can have left deeper.
            for (int i = 0; i < MATRIX_MODES.length; i++) {
                call("glMatrixMode", new Class<?>[]{int.class}, MATRIX_MODES[i]);
                for (int extra = depth(STACK_DEPTHS[i]) - depths[i]; extra > 0; extra--) {
                    call("glPopMatrix", new Class<?>[0]);
                }
            }
            call("glMatrixMode", new Class<?>[]{int.class}, GL_MODELVIEW);
            for (int extra = depth(STACK_DEPTHS[3]) - depths[3]; extra > 0; extra--) {
                call("glPopAttrib", new Class<?>[0]);
            }
        }
        return count;
    }

    private static final int[] MATRIX_MODES = {GL_MODELVIEW, GL_PROJECTION, GL_TEXTURE};
    /** The depths of the modelview, projection, texture and attribute stacks. */
    private static final int[] STACK_DEPTHS = {0x0BA3, 0x0BA4, 0x0BA5, 0x0BB0};

    private static int depth(int name) {
        return (Integer) call("glGetInteger", new Class<?>[]{int.class}, name);
    }

    private static void matrix(int mode, boolean push) {
        call("glMatrixMode", new Class<?>[]{int.class}, mode);
        if (push) {
            call("glPushMatrix", new Class<?>[0]);
            call("glLoadIdentity", new Class<?>[0]);
        } else {
            call("glPopMatrix", new Class<?>[0]);
        }
    }

    /** The faces in what feedback gave back, in blocks. */
    List<Face> parse(float[] values, String texture) {
        List<Face> faces = new ArrayList<>();
        Polygon pending = null;  // a triangle that may be half of a face the driver split in two
        int at = 0;
        while (at < values.length) {
            int token = (int) values[at++];
            switch (token) {
                case PASS_THROUGH:
                    int index = (int) values[at++];
                    texture = index >= 0 && index < textures.size() ? textures.get(index) : texture;
                    break;
                case POLYGON:
                    int corners = (int) values[at++];
                    if (at + corners * VERTEX > values.length) {
                        at = values.length;
                        break;
                    }
                    Polygon polygon = polygon(values, at, corners, texture);
                    at += corners * VERTEX;
                    if (polygon == null) {
                        break;
                    }
                    if (polygon.points.length == 3) {
                        float[][] quad = pending != null ? pending.joined(polygon) : null;
                        if (quad != null) {
                            faces.add(new Polygon(quad, texture, polygon.color).face(0));
                            pending = null;
                        } else {
                            fan(pending, faces);
                            pending = polygon;
                        }
                    } else {
                        fan(pending, faces);
                        pending = null;
                        fan(polygon, faces);
                    }
                    break;
                case POINT:
                case BITMAP:
                case DRAW_PIXEL:
                case COPY_PIXEL:
                    at += VERTEX;
                    break;
                case LINE:
                case LINE_RESET:
                    at += 2 * VERTEX;
                    break;
                default:
                    at = values.length;  // nothing else comes back; something is wrong
            }
        }
        fan(pending, faces);
        return faces;
    }

    /** A polygon as feedback gives it: its corners as x, y, z, u, v in blocks, its texture and its color. */
    private static final class Polygon {
        final float[][] points;
        final String texture;
        final int color;

        Polygon(float[][] points, String texture, int color) {
            this.points = points;
            this.texture = texture;
            this.color = color;
        }

        /** The face of four corners from a corner on: the next three, or the last again where there are fewer. */
        Face face(int from) {
            float[] corners = new float[20];
            float[][] four = {points[0], points[from + 1], points[from + 2],
                    points[Math.min(from + 3, points.length - 1)]};
            for (int k = 0; k < 4; k++) {
                System.arraycopy(four[k], 0, corners, k * 5, 5);
            }
            return new Face(corners, texture, color);
        }

        /**
         * This triangle and another as one face of four corners, where they share an edge and lie in one plane as
         * the two halves of a face do; null otherwise.
         */
        float[][] joined(Polygon other) {
            if (!texture.equals(other.texture) || color != other.color) {
                return null;
            }
            for (int i = 0; i < 3; i++) {
                float[] x = points[i];
                float[] y = points[(i + 1) % 3];
                float[] z = points[(i + 2) % 3];
                int xAt = indexOf(other.points, x);
                int yAt = indexOf(other.points, y);
                if (xAt < 0 || yAt < 0 || xAt == yAt) {
                    continue;  // not this edge, or a triangle with two corners in one place, as a flat page has
                }
                float[] s = other.points[3 - xAt - yAt];
                if (indexOf(points, s) >= 0 || !inPlane(x, y, z, s)) {
                    return null;
                }
                return new float[][]{x, s, y, z};
            }
            return null;
        }

        private static int indexOf(float[][] points, float[] point) {
            for (int i = 0; i < points.length; i++) {
                boolean same = true;
                for (int k = 0; k < 5; k++) {
                    same &= Math.abs(points[i][k] - point[k]) < 1e-4f;
                }
                if (same) {
                    return i;
                }
            }
            return -1;
        }

        private static boolean inPlane(float[] x, float[] y, float[] z, float[] s) {
            float ax = y[0] - x[0], ay = y[1] - x[1], az = y[2] - x[2];
            float bx = z[0] - x[0], by = z[1] - x[1], bz = z[2] - x[2];
            float nx = ay * bz - az * by, ny = az * bx - ax * bz, nz = ax * by - ay * bx;
            float length = (float) Math.sqrt(nx * nx + ny * ny + nz * nz);
            if (length < 1e-9f) {
                return false;
            }
            float distance = (nx * (s[0] - x[0]) + ny * (s[1] - x[1]) + nz * (s[2] - x[2])) / length;
            return Math.abs(distance) < 1e-3f;
        }
    }

    /** A polygon as faces of four corners: itself, or a fan of them from its first corner; a triangle repeats one. */
    private static void fan(Polygon polygon, List<Face> faces) {
        if (polygon == null) {
            return;
        }
        for (int i = 0; i + 2 < polygon.points.length; i += 2) {
            faces.add(polygon.face(i));
        }
    }

    private static Polygon polygon(float[] values, int at, int count, String texture) {
        if (count < 3 || texture == null) {
            return null;
        }
        float[][] points = new float[count][];
        float red = 0, green = 0, blue = 0;
        for (int i = 0; i < count; i++) {
            int v = at + i * VERTEX;
            float q = values[v + 10] != 0 ? values[v + 10] : 1;
            points[i] = new float[]{
                    RANGE * (2 * values[v] / SIZE - 1),
                    RANGE * (2 * values[v + 1] / SIZE - 1),
                    RANGE * (1 - 2 * values[v + 2]),
                    values[v + 7] / q,
                    values[v + 8] / q};
            red += values[v + 3];
            green += values[v + 4];
            blue += values[v + 5];
        }
        return new Polygon(points, texture, color(red / count, green / count, blue / count));
    }

    /**
     * Fill the empty fields of an object from the fields of another of the same types, as the block entity renderer
     * dispatcher gets the game's texture manager and font only once a world is drawn.
     */
    public static void fillEmptyFields(Object owner, Object source) {
        List<Object> values = fieldValues(source);
        for (Class<?> type = owner.getClass(); type != null && type != Object.class; type = type.getSuperclass()) {
            for (Field field : type.getDeclaredFields()) {
                Class<?> fieldType = field.getType();
                if (Modifier.isStatic(field.getModifiers()) || fieldType.isPrimitive() || fieldType == Object.class
                        || fieldType.isInterface() || fieldType == String.class || isWorldOrEntity(fieldType)) {
                    continue;
                }
                try {
                    field.setAccessible(true);
                    if (field.get(owner) != null) {
                        continue;
                    }
                    for (Object value : values) {
                        if (fieldType.isInstance(value)) {
                            field.set(owner, value);
                            break;
                        }
                    }
                } catch (ReflectiveOperationException | RuntimeException ignored) {
                    // left empty
                }
            }
        }
    }

    /**
     * The color a face was drawn in, without the light it was drawn in: lighting in OpenGL makes each color darker
     * by the same amount, so the color is what it is in its brightest channel.
     */
    static int color(float red, float green, float blue) {
        float most = Math.max(red, Math.max(green, blue));
        if (most <= 0.01f) {
            return 0xff000000;
        }
        int r = Math.round(Math.min(red / most, 1) * 255);
        int g = Math.round(Math.min(green / most, 1) * 255);
        int b = Math.round(Math.min(blue / most, 1) * 255);
        if (r >= 250 && g >= 250 && b >= 250) {
            return -1;
        }
        return 0xff000000 | r << 16 | g << 8 | b;
    }

    private static String key(Face face) {
        StringBuilder key = new StringBuilder(face.texture);
        for (float value : face.corners) {
            key.append(String.format(Locale.ROOT, ",%.4f", value));
        }
        return key.toString();
    }

    private static Class<?> gl11;
    private static final Map<String, Method> METHODS = new LinkedHashMap<>();

    private static Object call(String name, Class<?>[] types, Object... arguments) {
        try {
            if (gl11 == null) {
                gl11 = Class.forName("org.lwjgl.opengl.GL11", true, FeedbackCapture.class.getClassLoader());
            }
            Method method = METHODS.get(name);
            if (method == null) {
                method = gl11.getMethod(name, types);
                METHODS.put(name, method);
            }
            return method.invoke(null, arguments);
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException("OpenGL's " + name + " could not be called", e);
        }
    }

    /**
     * Put a stand-in in place of an object in the fields of some others that hold it, and of the objects in their
     * fields, as the texture manager is held by the game, the block entity renderers, the font and the item renderer.
     * Returns what puts them back.
     */
    public static Runnable swap(Object from, Object to, Object... owners) {
        List<Object[]> swapped = new ArrayList<>();
        List<Object> seen = new ArrayList<>();
        for (Object owner : owners) {
            swapIn(owner, from, to, swapped, seen);
            for (Object held : fieldValues(owner)) {
                swapIn(held, from, to, swapped, seen);
            }
        }
        return () -> {
            for (Object[] entry : swapped) {
                try {
                    ((Field) entry[1]).set(entry[0], from);
                } catch (ReflectiveOperationException | RuntimeException ignored) {
                    // nothing more can be done
                }
            }
        };
    }

    private static void swapIn(Object owner, Object from, Object to, List<Object[]> swapped, List<Object> seen) {
        if (owner == null || owner == from || owner == to || containsSame(seen, owner)) {
            return;
        }
        seen.add(owner);
        for (Class<?> type = owner.getClass(); type != null && type != Object.class; type = type.getSuperclass()) {
            for (Field field : type.getDeclaredFields()) {
                if (Modifier.isStatic(field.getModifiers()) || !field.getType().isInstance(to)) {
                    continue;
                }
                try {
                    field.setAccessible(true);
                    if (field.get(owner) == from) {
                        field.set(owner, to);
                        swapped.add(new Object[]{owner, field});
                    }
                } catch (ReflectiveOperationException | RuntimeException ignored) {
                    // a field that can't be set is left as it is
                }
            }
        }
    }

    private static boolean containsSame(List<Object> list, Object value) {
        for (Object item : list) {
            if (item == value) {
                return true;
            }
        }
        return false;
    }

    /** The objects an object holds in its fields, other than arrays, strings, numbers and collections. */
    private static List<Object> fieldValues(Object owner) {
        List<Object> values = new ArrayList<>();
        if (owner == null) {
            return values;
        }
        for (Class<?> type = owner.getClass(); type != null && type != Object.class; type = type.getSuperclass()) {
            for (Field field : type.getDeclaredFields()) {
                if (Modifier.isStatic(field.getModifiers()) || field.getType().isPrimitive() || field.getType().isArray()
                        || field.getType() == String.class) {
                    continue;
                }
                try {
                    field.setAccessible(true);
                    Object value = field.get(owner);
                    if (value != null && !(value instanceof Number) && !(value instanceof Map)
                            && !(value instanceof java.util.Collection)) {
                        values.add(value);
                    }
                } catch (ReflectiveOperationException | RuntimeException ignored) {
                    // a field that can't be read
                }
            }
        }
        return values;
    }

    /**
     * Whether a type is a world, an entity or what they are made of, by its name or those it extends, in any naming
     * of the game: those are not tools to lend a renderer, and the world the game has while one draws is a stand-in.
     */
    private static boolean isWorldOrEntity(Class<?> type) {
        for (Class<?> c = type; c != null && c != Object.class; c = c.getSuperclass()) {
            String name = c.getSimpleName();
            if (name.contains("World") || name.contains("Level") || name.contains("Entity") || name.contains("Player")) {
                return true;
            }
        }
        return false;
    }

    /**
     * An object of a class made without running any constructor, as a world of one block is: a real world can't be
     * made without a save and a server, and a renderer only asks it a few things, which the class answers itself.
     */
    @SuppressWarnings("unchecked")
    public static <T> T allocate(Class<T> type) {
        try {
            Class<?> unsafeType = Class.forName("sun.misc.Unsafe");
            Field field = unsafeType.getDeclaredField("theUnsafe");
            field.setAccessible(true);
            Object unsafe = field.get(null);
            return (T) unsafeType.getMethod("allocateInstance", Class.class).invoke(unsafe, type);
        } catch (ReflectiveOperationException | RuntimeException e) {
            return null;
        }
    }

    /** Set the first field of a type in an object's class or those it extends, as a block entity's cached state. */
    public static boolean setFieldOfType(Object owner, Class<?> declaredIn, Class<?> type, Object value) {
        for (Field field : declaredIn.getDeclaredFields()) {
            if (Modifier.isStatic(field.getModifiers()) || field.getType() != type) {
                continue;
            }
            try {
                field.setAccessible(true);
                field.set(owner, value);
                return true;
            } catch (ReflectiveOperationException | RuntimeException e) {
                return false;
            }
        }
        return false;
    }
}
