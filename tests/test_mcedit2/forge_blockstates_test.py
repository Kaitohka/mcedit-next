"""
    forge_blockstates_test

    Forge's own blockstate format, which many mods for 1.8.9 to 1.12.2 use.
"""
from mcedit2.rendering import forge_blockstates
from mcedit2.rendering.forge_blockstates import isForgeBlockstate, modelEntries, modelNames

LAMP = {
    "forge_marker": 1,
    "defaults": {"model": "cube_all", "textures": {"all": "somemod:blocks/lamp"}},
    "variants": {
        "lit": {"true": {"textures": {"all": "somemod:blocks/lamp_on"}}, "false": {}},
        "facing": {"north": {}, "east": {"y": 90}, "south": {"y": 180}, "west": {"y": 270}},
        "inventory": [{}],
    },
}


def testOnlyMarkedFilesAreForgeFiles():
    assert isForgeBlockstate(LAMP)
    assert not isForgeBlockstate({"variants": {"normal": {"model": "stone"}}})


def testPropertiesPartsGoOverTheDefaults():
    assert modelEntries(LAMP, "facing=east,lit=true") == [
        {"model": "cube_all", "y": 90, "textures": {"all": "somemod:blocks/lamp_on"}}]
    assert modelEntries(LAMP, "facing=north,lit=false") == [
        {"model": "cube_all", "textures": {"all": "somemod:blocks/lamp"}}]


def testAPartForTheWholeStateGoesOverThePropertiesParts():
    states = dict(LAMP, variants=dict(LAMP["variants"], **{
        "lit=true,facing=west": {"model": "somemod:lamp_special", "textures": {"side": "somemod:blocks/x"}}}))
    assert modelEntries(states, "facing=west,lit=true") == [
        {"model": "somemod:lamp_special", "y": 270,
         "textures": {"all": "somemod:blocks/lamp_on", "side": "somemod:blocks/x"}}]


def testABlockWithNoPropertiesUsesNormal():
    states = {"forge_marker": 1, "defaults": {"textures": {"all": "somemod:blocks/ore"}},
              "variants": {"normal": [{"model": "cube_all"}], "inventory": [{"model": "cube_all"}]}}
    assert modelEntries(states, "normal") == [{"model": "cube_all", "textures": {"all": "somemod:blocks/ore"}}]


def testAWholeStatePartOfOnlyTexturesIsNotTakenForAProperty():
    states = {"forge_marker": 1, "defaults": {"model": "cube_all"},
              "variants": {"normal": {"textures": {"all": "somemod:blocks/ore"}}}}
    assert modelEntries(states, "normal") == [{"model": "cube_all", "textures": {"all": "somemod:blocks/ore"}}]


def testSubmodelsAreDrawnTooAndTurnWithTheBlock():
    states = {"forge_marker": 1,
              "defaults": {"model": "somemod:pipe_core", "submodel": {"north": {"model": "somemod:pipe_arm"}}},
              "variants": {"facing": {"north": {}, "east": {"y": 90}},
                           "down": {"true": {"submodel": {"down": {"model": "somemod:pipe_arm", "x": 90}}},
                                    "false": {}}}}
    assert modelEntries(states, "down=true,facing=east") == [
        {"model": "somemod:pipe_core", "y": 90},
        {"model": "somemod:pipe_arm", "x": 0, "y": 90},
        {"model": "somemod:pipe_arm", "x": 90, "y": 90},
    ]


def testAStateWithNoModelHasNoEntries():
    states = {"forge_marker": 1, "variants": {"normal": [{}]}}
    assert modelEntries(states, "normal") == []


def testEveryModelNamedIsFound():
    states = dict(LAMP, variants=dict(LAMP["variants"], **{
        "lit=true,facing=west": {"model": "somemod:lamp_special", "submodel": "somemod:glow"}}))
    assert sorted(modelNames(states)) == ["cube_all", "somemod:glow", "somemod:lamp_special"]
    assert forge_blockstates.PART_KEYS >= {"model", "textures", "submodel"}
