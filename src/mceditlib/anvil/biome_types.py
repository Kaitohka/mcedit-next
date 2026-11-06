"""
   biome_types.py

   Definitions for biome names, climate info, water color, and more.
"""

from collections import namedtuple
import csv
import os

_BiomeType = namedtuple("BiomeType", "ID name temperature rainfall")

class BiomeType(_BiomeType):
    pass


class BiomeTypes(object):
    def __init__(self):
        self.types = {}

        path = os.path.join(os.path.dirname(__file__), "biomes.csv")
        with open(path, newline="", encoding="utf-8") as f:
            for record in csv.reader(f):
                if record[0] == "ID":
                    # headers: ID,Name,Temperature,Rainfall
                    continue

                ID, name, temp, rain = record[:4]
                self.types[int(ID)] = BiomeType(int(ID), name, float(temp), float(rain))

