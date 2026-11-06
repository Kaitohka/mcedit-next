package net.mcedit.exporter.testmod;

import com.mojang.blaze3d.vertex.PoseStack;
import net.minecraft.client.renderer.MultiBufferSource;
import net.minecraft.client.renderer.blockentity.BlockEntityRenderer;
import net.minecraft.client.renderer.blockentity.BlockEntityRendererProvider;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.HolderLookup;
import net.minecraft.core.registries.Registries;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.world.level.BlockGetter;
import net.minecraft.world.level.EmptyBlockGetter;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.EntityBlock;
import net.minecraft.world.level.block.RenderShape;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.block.entity.BlockEntityType;
import net.minecraft.world.level.block.state.BlockBehaviour;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.StateDefinition;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.level.block.state.properties.DirectionProperty;
import net.minecraft.world.level.material.MapColor;
import net.minecraft.world.phys.shapes.CollisionContext;
import net.minecraft.world.phys.shapes.Shapes;
import net.minecraft.world.phys.shapes.VoxelShape;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.client.event.EntityRenderersEvent;
import net.neoforged.neoforge.registries.DeferredBlock;
import net.neoforged.neoforge.registries.DeferredHolder;
import net.neoforged.neoforge.registries.DeferredRegister;

/**
 * One block of each kind that the exporter has to deal with. For testing only, and not part of the built mod.
 * tests/test_mcedit2/exporter_pipeline_test.py expects these blocks.
 */
@Mod(value = TestMod.ID, dist = Dist.CLIENT)
public final class TestMod {
    static final String ID = "mcedit_testmod";

    private static final DeferredRegister.Blocks BLOCKS = DeferredRegister.createBlocks(ID);
    private static final DeferredRegister<BlockEntityType<?>> BLOCK_ENTITIES =
            DeferredRegister.create(Registries.BLOCK_ENTITY_TYPE, ID);

    private static BlockBehaviour.Properties solid() {
        return BlockBehaviour.Properties.of().mapColor(MapColor.STONE);
    }

    /** A cube with an ordinary model and an animated texture. */
    private static final DeferredBlock<Block> PLAIN = BLOCKS.registerSimpleBlock("plain", solid());

    /** Has a property, and a model turned by its blockstate file. */
    private static final DeferredBlock<Block> ORIENTED = BLOCKS.registerBlock("oriented", OrientedBlock::new, solid());

    /** Drawn only by a block entity renderer, with a model that has nothing but a particle texture. */
    private static final DeferredBlock<Block> RENDER_ONLY = BLOCKS.registerBlock("render_only", RenderOnlyBlock::new,
            solid().noOcclusion());

    /** Has a model that needs a custom model loader. */
    private static final DeferredBlock<Block> LOADER_MODEL = BLOCKS.registerSimpleBlock("loader_model", solid());

    /** Throws when asked for its shape with no world to look at, as blocks from mods can. */
    private static final DeferredBlock<Block> FRAGILE = BLOCKS.registerBlock("fragile", FragileBlock::new,
            solid().dynamicShape().noOcclusion());

    /** Has no resource files at all. */
    private static final DeferredBlock<Block> NO_ASSETS = BLOCKS.registerSimpleBlock("no_assets", solid());

    private static final DeferredHolder<BlockEntityType<?>, BlockEntityType<RenderOnlyEntity>> RENDER_ONLY_ENTITY =
            BLOCK_ENTITIES.register("render_only", () -> BlockEntityType.Builder
                    .of(RenderOnlyEntity::new, RENDER_ONLY.get()).build(null));

    /** Fails when saved, which is what the export has to do to find its default NBT. */
    private static final DeferredHolder<BlockEntityType<?>, BlockEntityType<FragileEntity>> FRAGILE_ENTITY =
            BLOCK_ENTITIES.register("fragile_entity", () -> BlockEntityType.Builder
                    .of(FragileEntity::new, RENDER_ONLY.get()).build(null));

    public TestMod(IEventBus modBus) {
        BLOCKS.register(modBus);
        BLOCK_ENTITIES.register(modBus);
        modBus.addListener(TestMod::registerRenderers);
    }

    private static void registerRenderers(EntityRenderersEvent.RegisterRenderers event) {
        event.registerBlockEntityRenderer(RENDER_ONLY_ENTITY.get(), NothingRenderer::new);
    }

    private static final class FragileBlock extends Block {
        FragileBlock(Properties properties) {
            super(properties);
        }

        @Override
        protected VoxelShape getShape(BlockState state, BlockGetter level, BlockPos pos, CollisionContext context) {
            if (level == EmptyBlockGetter.INSTANCE) {
                throw new IllegalStateException("This block needs a world");
            }
            return Shapes.block();
        }
    }

    private static final class OrientedBlock extends Block {
        private static final DirectionProperty FACING = BlockStateProperties.HORIZONTAL_FACING;

        OrientedBlock(Properties properties) {
            super(properties);
            registerDefaultState(stateDefinition.any().setValue(FACING, Direction.NORTH));
        }

        @Override
        protected void createBlockStateDefinition(StateDefinition.Builder<Block, BlockState> builder) {
            builder.add(FACING);
        }
    }

    private static final class RenderOnlyBlock extends Block implements EntityBlock {
        RenderOnlyBlock(Properties properties) {
            super(properties);
        }

        @Override
        protected RenderShape getRenderShape(BlockState state) {
            return RenderShape.ENTITYBLOCK_ANIMATED;
        }

        @Override
        public BlockEntity newBlockEntity(BlockPos pos, BlockState state) {
            return new RenderOnlyEntity(pos, state);
        }
    }

    private static final class RenderOnlyEntity extends BlockEntity {
        RenderOnlyEntity(BlockPos pos, BlockState state) {
            super(RENDER_ONLY_ENTITY.get(), pos, state);
        }

        @Override
        protected void saveAdditional(CompoundTag tag, HolderLookup.Provider registries) {
            super.saveAdditional(tag, registries);
            tag.putInt("charge", 7);
            tag.putString("label", "new");
        }
    }

    private static final class FragileEntity extends BlockEntity {
        FragileEntity(BlockPos pos, BlockState state) {
            super(FRAGILE_ENTITY.get(), pos, state);
        }

        @Override
        protected void saveAdditional(CompoundTag tag, HolderLookup.Provider registries) {
            throw new IllegalStateException("This block entity needs a world");
        }
    }

    private static final class NothingRenderer implements BlockEntityRenderer<RenderOnlyEntity> {
        NothingRenderer(BlockEntityRendererProvider.Context context) {
        }

        @Override
        public void render(RenderOnlyEntity entity, float partialTick, PoseStack poseStack, MultiBufferSource buffers,
                           int packedLight, int packedOverlay) {
        }
    }
}
