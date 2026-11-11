package net.mcedit.exporter.testmod;

import com.google.gson.JsonDeserializationContext;
import com.google.gson.JsonObject;
import com.mojang.blaze3d.vertex.PoseStack;
import com.mojang.blaze3d.vertex.VertexConsumer;
import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.MultiBufferSource;
import net.minecraft.client.renderer.RenderType;
import net.minecraft.client.renderer.block.model.BakedQuad;
import net.minecraft.client.renderer.block.model.ItemOverrides;
import net.minecraft.client.renderer.texture.TextureAtlasSprite;
import net.minecraft.client.resources.model.BakedModel;
import net.minecraft.client.resources.model.Material;
import net.minecraft.client.resources.model.ModelBaker;
import net.minecraft.client.resources.model.ModelState;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.inventory.InventoryMenu;
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
import net.neoforged.neoforge.client.RenderTypeGroup;
import net.neoforged.neoforge.client.event.EntityRenderersEvent;
import net.neoforged.neoforge.client.event.ModelEvent;
import net.neoforged.neoforge.client.model.IModelBuilder;
import net.neoforged.neoforge.client.model.geometry.IGeometryBakingContext;
import net.neoforged.neoforge.client.model.geometry.IGeometryLoader;
import net.neoforged.neoforge.client.model.geometry.IUnbakedGeometry;
import net.neoforged.neoforge.client.model.pipeline.QuadBakingVertexConsumer;
import net.neoforged.neoforge.registries.DeferredBlock;
import net.neoforged.neoforge.registries.DeferredHolder;
import net.neoforged.neoforge.registries.DeferredRegister;

import java.util.function.Function;

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

    /** Has a Wavefront .obj model, read by NeoForge's OBJ loader. */
    private static final DeferredBlock<Block> OBJ_MODEL = BLOCKS.registerSimpleBlock("obj_model",
            solid().noOcclusion());

    /** Has a model this mod makes in code, from a model loader of its own: a slope, and the bottom under it. */
    private static final DeferredBlock<Block> CODE_MODEL = BLOCKS.registerSimpleBlock("code_model",
            solid().noOcclusion());

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
        modBus.addListener(TestMod::registerLoaders);
    }

    private static void registerRenderers(EntityRenderersEvent.RegisterRenderers event) {
        event.registerBlockEntityRenderer(RENDER_ONLY_ENTITY.get(), BoxRenderer::new);
    }

    private static void registerLoaders(ModelEvent.RegisterGeometryLoaders event) {
        event.register(ResourceLocation.fromNamespaceAndPath(ID, "code_model"), CodeModelLoader.INSTANCE);
    }

    private enum CodeModelLoader implements IGeometryLoader<CodeModel> {
        INSTANCE;

        @Override
        public CodeModel read(JsonObject json, JsonDeserializationContext context) {
            return new CodeModel();
        }
    }

    private static final class CodeModel implements IUnbakedGeometry<CodeModel> {
        @Override
        public BakedModel bake(IGeometryBakingContext context, ModelBaker baker,
                               Function<Material, TextureAtlasSprite> spriteGetter, ModelState modelState,
                               ItemOverrides overrides) {
            TextureAtlasSprite sprite = spriteGetter.apply(context.getMaterial("particle"));
            IModelBuilder<?> builder = IModelBuilder.of(true, true, false, context.getTransforms(), overrides, sprite,
                    RenderTypeGroup.EMPTY);
            builder.addCulledFace(Direction.DOWN, quad(sprite, Direction.DOWN, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0, 1));
            builder.addUnculledFace(quad(sprite, Direction.UP, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 0, 0));
            return builder.build();
        }

        private static BakedQuad quad(TextureAtlasSprite sprite, Direction direction, float... xyz) {
            QuadBakingVertexConsumer baker = new QuadBakingVertexConsumer();
            baker.setSprite(sprite);
            baker.setDirection(direction);
            baker.setShade(true);
            float[][] uv = {{0, 0}, {0, 1}, {1, 1}, {1, 0}};
            for (int i = 0; i < 4; i++) {
                baker.addVertex(xyz[i * 3], xyz[i * 3 + 1], xyz[i * 3 + 2]).setColor(-1)
                        .setUv(sprite.getU(uv[i][0]), sprite.getV(uv[i][1])).setUv2(0, 0)
                        .setNormal(direction.getStepX(), direction.getStepY(), direction.getStepZ());
            }
            return baker.bakeQuad();
        }
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

    /**
     * Draws the top of a box with a texture of its own, as entity models are drawn, and a plane on the north side
     * with a texture from the block atlas.
     */
    private static final class BoxRenderer implements BlockEntityRenderer<RenderOnlyEntity> {
        private static final ResourceLocation BOX = ResourceLocation.fromNamespaceAndPath(ID,
                "textures/entity/render_only_box.png");

        BoxRenderer(BlockEntityRendererProvider.Context context) {
        }

        @Override
        public void render(RenderOnlyEntity entity, float partialTick, PoseStack poseStack, MultiBufferSource buffers,
                           int packedLight, int packedOverlay) {
            PoseStack.Pose pose = poseStack.last();
            VertexConsumer box = buffers.getBuffer(RenderType.entityCutout(BOX));
            float[][] top = {{0.25f, 0.5f, 0.25f, 0, 0}, {0.25f, 0.5f, 0.75f, 0, 1}, {0.75f, 0.5f, 0.75f, 1, 1},
                    {0.75f, 0.5f, 0.25f, 1, 0}};
            for (float[] v : top) {
                box.addVertex(pose, v[0], v[1], v[2]).setColor(-1).setUv(v[3], v[4]).setOverlay(packedOverlay)
                        .setLight(packedLight).setNormal(pose, 0, 1, 0);
            }
            TextureAtlasSprite sprite = Minecraft.getInstance().getTextureAtlas(InventoryMenu.BLOCK_ATLAS)
                    .apply(ResourceLocation.fromNamespaceAndPath(ID, "block/render_only"));
            VertexConsumer plane = buffers.getBuffer(RenderType.cutout());
            float[][] north = {{1, 1, 0, 0, 0}, {1, 0, 0, 0, 1}, {0, 0, 0, 1, 1}, {0, 1, 0, 1, 0}};
            for (float[] v : north) {
                plane.addVertex(pose, v[0], v[1], v[2]).setColor(-1).setUv(sprite.getU(v[3]), sprite.getV(v[4]))
                        .setOverlay(packedOverlay).setLight(packedLight).setNormal(pose, 0, 0, -1);
            }
        }
    }
}
