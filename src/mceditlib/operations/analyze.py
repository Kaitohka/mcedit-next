"""
   analyze.py

   Get counts of blocks, entities, and tile entities in a selection.
"""
import logging

import numpy
from collections import defaultdict
from mceditlib.operations import Operation

log = logging.getLogger(__name__)

# Blocks are counted by ID and meta packed into one number, with the meta above the 16 bits of ID.
DATA_SHIFT = 16
ID_MASK = (1 << DATA_SHIFT) - 1
DATA_SHIFT_SIZE = 16 << DATA_SHIFT


class AnalyzeOperation(Operation):
    def __init__(self, dimension, selection):
        """
        Analyze all blocks in a selection and return counts of block types, entity IDs and tile entity IDs.

        Counts are returned in `self.blocks`, `self.entityCounts` and `self.tileEntityCounts`

        :type dimension: WorldEditorDimension
        :type selection: `~.BoundingBox`
        """
        super(AnalyzeOperation, self).__init__(dimension, selection)

        self.createSections = False
        self.blocks = numpy.zeros(DATA_SHIFT_SIZE, dtype='intp')
        self.selection = selection
        self.entityCounts = defaultdict(int)
        self.tileEntityCounts = defaultdict(int)

        self.skipped = 0
        self.sections = 0
        log.info("Analyzing %s blocks", selection.volume)

    def done(self):
        log.info(u"Analyze: Skipped {0}/{1} sections".format(self.skipped, self.sections))

    def operateOnChunk(self, chunk):
        cx, cz = chunk.cx, chunk.cz
        
        for cy in chunk.bounds.sectionPositions(cx, cz):
            section = chunk.getSection(cy, create=False)

            if section is None:
                continue
            self.sections += 1

            sectionMask = self.selection.section_mask(cx, cy, cz)
            if sectionMask is None:
                self.skipped += 1
                continue

            maskSize = sectionMask.sum()
            if maskSize == 0:
                self.skipped += 1
                continue

            blocks = numpy.array(section.Blocks[sectionMask], dtype='uint32')
            blocks |= (numpy.array(section.Data[sectionMask], dtype='uint32') << DATA_SHIFT)
            b = numpy.bincount(blocks.ravel())

            self.blocks[:b.shape[0]] += b

        for ref in chunk.Entities:
            if ref.Position in self.selection:
                self.entityCounts[ref.id] += 1

        for ref in chunk.TileEntities:
            if ref.Position in self.selection:
                self.tileEntityCounts[ref.id] += 1




