"""
    biome_colors

    The colors that biomes give to grass, foliage and water, as tables with one row per biome ID that
    a chunk's Biomes array can hold. Each row is in the byte order of a QImage's ARGB32 pixel (BGRA).
"""
import logging

import numpy
from PySide6 import QtGui

from mceditlib.anvil.biome_types import BiomeTypes

log = logging.getLogger(__name__)

BIOME_COUNT = 256
WHITE = 0xffffff
DEFAULT_WATER_COLOR = 0x3f76e4
DEFAULT_TEMPERATURE = 0.8  # plains, for biomes with no definition to go on
DEFAULT_DOWNFALL = 0.4
SWAMP_GRASS_COLOR = 0x6a7039  # the game varies this with position, between this and a darker color
DARK_FOREST_GRASS_OFFSET = 0x28340a


def _loadColormap(resourceLoader, name):
    image = QtGui.QImage.fromData(resourceLoader.openStream("assets/minecraft/textures/colormap/%s.png" % name).read())
    image = image.convertToFormat(QtGui.QImage.Format_ARGB32)
    bits = numpy.frombuffer(image.constBits(), dtype="uint8").copy()
    return bits.reshape(image.height(), image.bytesPerLine() // 4, 4)


def _colorsFromClimate(colormap, temperatures, rainfalls):
    temperature = numpy.clip(temperatures, 0.0, 1.0)
    rainfall = numpy.clip(rainfalls, 0.0, 1.0) * temperature
    height, width = colormap.shape[:2]
    x = ((1.0 - temperature) * (width - 1)).astype("intp")
    y = ((1.0 - rainfall) * (height - 1)).astype("intp")
    return colormap[y, x].copy()


def _pixel(color):
    return color & 0xff, (color >> 8) & 0xff, (color >> 16) & 0xff, 0xff


def _color(pixel):
    return int(pixel[0]) | int(pixel[1]) << 8 | int(pixel[2]) << 16


class BiomeColors(object):
    """
    :ivar grass: Colors for blocks tinted like grass.
    :ivar foliage: Colors for blocks tinted like leaves.
    :ivar water: Colors to tint the water texture with. White if the texture already has its color.
    """

    def __init__(self, grass, foliage, water):
        self.grass = grass
        self.foliage = foliage
        self.water = water


def legacyBiomeColors(resourceLoader):
    """ Colors for worlds whose chunks hold the game's own numbers for biomes. """
    temperatures = numpy.zeros(BIOME_COUNT, "float32")
    rainfalls = numpy.zeros(BIOME_COUNT, "float32")
    for biome in BiomeTypes().types.values():
        temperatures[biome.ID] = biome.temperature
        rainfalls[biome.ID] = biome.rainfall

    return BiomeColors(_colorsFromClimate(_loadColormap(resourceLoader, "grass"), temperatures, rainfalls),
                       _colorsFromClimate(_loadColormap(resourceLoader, "foliage"), temperatures, rainfalls),
                       numpy.full((BIOME_COUNT, 4), 0xff, "uint8"))


def modernBiomeColors(resourceLoader, adapter):
    """
    Colors from the biome definitions in the game and any mods. Every biome found gets its ID from the
    adapter now, so that it has a row here even if no chunk has been read with it yet. A biome that
    is seen later with no definition gets the default colors.
    """
    temperatures = numpy.full(BIOME_COUNT, DEFAULT_TEMPERATURE, "float32")
    rainfalls = numpy.full(BIOME_COUNT, DEFAULT_DOWNFALL, "float32")
    water = numpy.zeros((BIOME_COUNT, 4), "uint8")
    water[:] = _pixel(DEFAULT_WATER_COLOR)

    definitions = {}
    for name, definition in resourceLoader.biomeDefinitions():
        ID = adapter.biomeID(name)
        definitions[ID] = definition
        temperatures[ID] = definition.get("temperature", DEFAULT_TEMPERATURE)
        rainfalls[ID] = definition.get("downfall", DEFAULT_DOWNFALL)
        water[ID] = _pixel(definition.get("effects", {}).get("water_color", DEFAULT_WATER_COLOR))

    grass = _colorsFromClimate(_loadColormap(resourceLoader, "grass"), temperatures, rainfalls)
    foliage = _colorsFromClimate(_loadColormap(resourceLoader, "foliage"), temperatures, rainfalls)

    for ID, definition in definitions.items():
        effects = definition.get("effects", {})
        if "grass_color" in effects:
            grass[ID] = _pixel(effects["grass_color"])
        if "foliage_color" in effects:
            foliage[ID] = _pixel(effects["foliage_color"])
        modifier = effects.get("grass_color_modifier")
        if modifier == "swamp":
            grass[ID] = _pixel(SWAMP_GRASS_COLOR)
        elif modifier == "dark_forest":
            grass[ID] = _pixel(((_color(grass[ID]) & 0xfefefe) + DARK_FOREST_GRASS_OFFSET) >> 1)

    log.info("Loaded colors for %d biomes", len(definitions))
    return BiomeColors(grass, foliage, water)


def biomeColorsForWorld(world, resourceLoader):
    adapter = getattr(world, "adapter", None)
    if hasattr(adapter, "biomeID"):
        return modernBiomeColors(resourceLoader, adapter)
    return legacyBiomeColors(resourceLoader)
