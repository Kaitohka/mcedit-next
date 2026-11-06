"""
    ${NAME}
"""

import logging

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

log = logging.getLogger(__name__)

class Layer:
    Blocks = "Blocks"
    Entities = "Entities"
    Monsters = "Monsters"
    MonsterLocations = "MonsterLocations"
    Items = "Items"
    TileEntities = "TileEntities"
    TileEntityLocations = "TileEntityLocations"
    CommandBlockColors = "CommandBlockColors"
    CommandBlockLocations = "CommandBlockLocations"
    ItemFrames = "ItemFrames"
    TileTicks = "TileTicks"
    TerrainPopulated = "TerrainPopulated"
    HeightMap = "HeightMap"
    MobSpawns = "Places Where Creepers Can Spawn"
    ChunkSections = "ChunkSections"
    AllLayers = (Blocks, MonsterLocations, Items,
                 TileEntities, TileEntityLocations,
                 CommandBlockColors, CommandBlockLocations,
                 ItemFrames, TileTicks, ChunkSections, HeightMap,
                 MobSpawns)
    DefaultVisibleLayers = (Blocks, Items, TileEntities, CommandBlockColors, ItemFrames)


# Names for the "Show..." menu. The layer names themselves are kept in the settings.
_displayNames = {
    Layer.Blocks: QT_TRANSLATE_NOOP("Layer", "Blocks"),
    Layer.MonsterLocations: QT_TRANSLATE_NOOP("Layer", "Mob Locations"),
    Layer.Items: QT_TRANSLATE_NOOP("Layer", "Items"),
    Layer.TileEntities: QT_TRANSLATE_NOOP("Layer", "Block Entities"),
    Layer.TileEntityLocations: QT_TRANSLATE_NOOP("Layer", "Block Entity Locations"),
    Layer.CommandBlockColors: QT_TRANSLATE_NOOP("Layer", "Command Block Colors"),
    Layer.CommandBlockLocations: QT_TRANSLATE_NOOP("Layer", "Command Block Locations"),
    Layer.ItemFrames: QT_TRANSLATE_NOOP("Layer", "Item Frames"),
    Layer.TileTicks: QT_TRANSLATE_NOOP("Layer", "Scheduled Ticks"),
    Layer.ChunkSections: QT_TRANSLATE_NOOP("Layer", "Chunk Sections"),
    Layer.HeightMap: QT_TRANSLATE_NOOP("Layer", "Height Map"),
    Layer.MobSpawns: QT_TRANSLATE_NOOP("Layer", "Places Where Creepers Can Spawn"),
}


def layerDisplayName(layer):
    return QCoreApplication.translate("Layer", _displayNames.get(layer, layer))
