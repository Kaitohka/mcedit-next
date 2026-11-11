package net.mcedit.exporter.export;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;

/**
 * One face of four corners as the game draws it, for a block whose look its model files can't give: one the game
 * makes in code, or one drawn by a block entity renderer. Positions are in blocks, with the block from 0 to 1 on
 * each axis; texture coordinates are from 0 to 1 across the texture, with v from its top.
 */
public final class Quad {
    /** x, y, z, u, v of each corner */
    public final float[] corners;
    /**
     * The texture: a sprite of the block atlas as "namespace:path", or a whole texture file, such as an entity's,
     * as "namespace:textures/entity/thing.png".
     */
    public final String texture;
    /** The side the face looks toward, as "north", for its shading. */
    public final String side;
    /** The side whose neighbor hides the face, or null. */
    public final String cullface;
    public final int tintIndex;
    public final boolean shade;
    /** The face's color as 0xAARRGGBB, which the game multiplies its texture by; -1 for none. */
    public final int color;

    public Quad(float[] corners, String texture, String side, String cullface, int tintIndex, boolean shade, int color) {
        if (corners.length != 20) {
            throw new IllegalArgumentException("a quad has 4 corners of 5 numbers, not " + corners.length);
        }
        this.corners = corners;
        this.texture = texture;
        this.side = side;
        this.cullface = cullface;
        this.tintIndex = tintIndex;
        this.shade = shade;
        this.color = color;
    }

    public static final String[] SIDES = {"down", "up", "north", "south", "west", "east"};
    private static final float[][] SIDE_VECTORS = {{0, -1, 0}, {0, 1, 0}, {0, 0, -1}, {0, 0, 1}, {-1, 0, 0}, {1, 0, 0}};

    /**
     * A face from the game's packed vertex data, as a baked model or a buffer holds it: four vertices of the same
     * number of ints each, with the position in the first three and the texture coordinates in the fifth and
     * sixth, in every version from 1.8 on. The texture coordinates are on the atlas, and are made relative to the
     * sprite, which spans u0..u1 and v0..v1 of it.
     */
    public static Quad fromVertexData(int[] data, String sprite, float u0, float u1, float v0, float v1,
                                      String side, String cullface, int tintIndex, boolean shade) {
        int stride = data.length / 4;
        float[] corners = new float[20];
        for (int i = 0; i < 4; i++) {
            int at = i * stride;
            corners[i * 5] = Float.intBitsToFloat(data[at]);
            corners[i * 5 + 1] = Float.intBitsToFloat(data[at + 1]);
            corners[i * 5 + 2] = Float.intBitsToFloat(data[at + 2]);
            corners[i * 5 + 3] = relative(Float.intBitsToFloat(data[at + 4]), u0, u1);
            corners[i * 5 + 4] = relative(Float.intBitsToFloat(data[at + 5]), v0, v1);
        }
        return new Quad(corners, sprite, side != null ? side : nearestSide(corners), cullface, tintIndex, shade, -1);
    }

    static float relative(float value, float from, float to) {
        return to == from ? 0 : (value - from) / (to - from);
    }

    /** The side the face looks toward most, from the winding of its corners, as the game picks the nearest. */
    public static String nearestSide(float[] c) {
        float ax = c[5] - c[0], ay = c[6] - c[1], az = c[7] - c[2];
        float bx = c[10] - c[0], by = c[11] - c[1], bz = c[12] - c[2];
        float nx = ay * bz - az * by, ny = az * bx - ax * bz, nz = ax * by - ay * bx;
        if (nx == 0 && ny == 0 && nz == 0) {
            // A triangle with its first corners the same: use the last.
            bx = c[15] - c[0];
            by = c[16] - c[1];
            bz = c[17] - c[2];
            ax = c[10] - c[0];
            ay = c[11] - c[1];
            az = c[12] - c[2];
            nx = ay * bz - az * by;
            ny = az * bx - ax * bz;
            nz = ax * by - ay * bx;
        }
        return nearestSide(nx, ny, nz);
    }

    public static String nearestSide(float nx, float ny, float nz) {
        String best = "up";
        float bestDot = -Float.MAX_VALUE;
        for (int i = 0; i < 6; i++) {
            float dot = nx * SIDE_VECTORS[i][0] + ny * SIDE_VECTORS[i][1] + nz * SIDE_VECTORS[i][2];
            if (dot > bestDot) {
                bestDot = dot;
                best = SIDES[i];
            }
        }
        return best;
    }

    JsonObject toJson(String textureKey) {
        JsonObject json = new JsonObject();
        JsonArray vertices = new JsonArray();
        for (int i = 0; i < 4; i++) {
            JsonArray vertex = new JsonArray();
            for (int j = 0; j < 5; j++) {
                vertex.add(new JsonPrimitive(round(corners[i * 5 + j])));
            }
            vertices.add(vertex);
        }
        json.add("vertices", vertices);
        json.addProperty("texture", "#" + textureKey);
        json.addProperty("side", side);
        if (cullface != null) {
            json.addProperty("cullface", cullface);
        }
        if (tintIndex >= 0) {
            json.addProperty("tintindex", tintIndex);
        }
        if (!shade) {
            json.addProperty("shade", false);
        }
        if (color != -1) {
            json.addProperty("color", String.format("%08x", color));
        }
        return json;
    }

    /** Enough places for a sixteenth of a pixel, and no more, so that alike faces are written alike. */
    private static Number round(float value) {
        double rounded = Math.round(value * 65536.0) / 65536.0;
        return rounded == Math.rint(rounded) ? (Number) (long) rounded : (Number) rounded;
    }
}
