"""
    modern_rotation

    Tables for turning block states from Minecraft 1.13 and later. All of a block's state is in its properties, and
    the names of the properties say how they turn, so this works for the blocks of any mod that uses the
    usual names. A state with nowhere to turn to stays as it is, as for stairs turned on their side.
"""
from mceditlib.blocktypes.rotation import BlockRotations, blankRotationTable

DIRECTION_NAMES = ("north", "south", "east", "west", "up", "down")
SIXTEENTHS_PER_QUARTER = 4


def turnDirection(direction, axis):
    return BlockRotations.mappings[axis].get(direction, direction)


def turnShape(shape, axis):
    """ Turn a value made of directions joined by "_", like the shapes of rails. """
    parts = shape.split("_")
    return "_".join(turnDirection(part, axis) if part in DIRECTION_NAMES else part for part in parts)


RAIL_SHAPES = ("north_south", "east_west", "ascending_north", "ascending_south", "ascending_east", "ascending_west",
               "south_east", "south_west", "north_east", "north_west")


def turnRailShape(shape, axis):
    if axis != "y":
        return shape
    turned = turnShape(shape, axis)
    if turned in RAIL_SHAPES:
        return turned
    # Curves are named south before north, and east before west... but only one order of the two exists.
    first, _, second = turned.partition("_")
    return second + "_" + first


def turnState(state, axis):
    """
    Return the given state turned by a quarter turn around the axis.

    :param state: Properties and their values
    :type state: dict
    :type axis: str
    :rtype: dict
    """
    turned = {}
    for key, value in state.items():
        if key in DIRECTION_NAMES:
            # Which sides a block connects on, as for fences and walls: the sides move, keeping their values.
            turned[turnDirection(key, axis)] = value
        elif key == "facing" or key.endswith("_facing"):
            turned[key] = turnDirection(value, axis)
        elif key == "axis":
            turned[key] = BlockRotations.axisMappings[axis].get(value, value)
        elif key == "rotation" and axis == "y":
            # In sixteenths of a turn clockwise when looking down, and these tables turn the other way.
            turned[key] = str((int(value) - SIXTEENTHS_PER_QUARTER) % 16)
        elif key == "shape" and value in RAIL_SHAPES:
            turned[key] = turnRailShape(value, axis)
        elif key == "orientation":
            turned[key] = turnShape(value, axis)
        else:
            turned[key] = value
    return turned


class ModernBlockRotations(object):
    """
    The same tables as BlockRotations. Each is only made when it is first asked for. With every state of a game and
    its mods to go through, making them all would be noticeably slow.
    """

    def __init__(self, blocktypes):
        self.blocktypes = blocktypes
        self._tables = {}

    def _table(self, axis, quarters):
        key = axis, quarters
        table = self._tables.get(key)
        if table is None:
            table = self._tables[key] = self._buildTable(axis, quarters)
        return table

    def _buildTable(self, axis, quarters):
        blocktypes = self.blocktypes
        table = blankRotationTable()
        for pair, nameAndState in blocktypes.statesByID.items():
            name, _, stateText = nameAndState.partition("[")
            if not stateText:
                continue
            state = dict(part.split("=", 1) for part in stateText.rstrip("]").split(","))
            for _ in range(quarters):
                state = turnState(state, axis)
            turned = "%s[%s]" % (name, ",".join("%s=%s" % item for item in sorted(state.items())))
            target = blocktypes.IDsByState.get(turned)
            if target is not None:
                table[pair[0], pair[1]] = target
        return table

    @property
    def rotateY90(self):
        return self._table("y", 1)

    @property
    def rotateX90(self):
        return self._table("x", 1)

    @property
    def rotateZ90(self):
        return self._table("z", 1)

    @property
    def rotateX180(self):
        return self._table("x", 2)

    @property
    def rotateZ180(self):
        return self._table("z", 2)


def rotationsFor(blocktypes):
    """ Return the rotation tables for a set of block types, which are kept for as long as the set is. """
    rotations = getattr(blocktypes, "_rotations", None)
    if rotations is None:
        rotations = ModernBlockRotations(blocktypes) if hasattr(blocktypes, "idAndMeta") else BlockRotations(blocktypes)
        blocktypes._rotations = rotations
    return rotations
