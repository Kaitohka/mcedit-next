# MCEdit 2 升级方案：Python 3 + 1.21 世界读取 + 自定义方块 + 配套导出模组

| 项 | 内容 |
|---|---|
| 状态 | 当前方案（替代 `REMAKE_PLAN.md` 的重写路线），待确认第 11 节 |
| 编写日期 | 2026-11-05 |
| 目标 | 在现有代码上升级：Python 3、读取 1.21.1 世界、外部资源自定义方块、客户端导出模组 |
| 首个版本 | Minecraft 1.21.1；导出模组为 NeoForge 21.1.x |
| 版本冗余 | 世界格式按 DataVersion 分配置，导出包有独立版本号，详见第 8 节 |

标注：**[待验证]** 表示来自经验判断、未实测。旧代码未运行过，下文对旧行为的描述都来自读代码。

---

## 1. 关键判断

### 1.1 四条需求的结论

| 需求 | 结论 | 说明 |
|---|---|---|
| 1. 换成 Python 3 | 可行 | Qt4 的 PySide1 没有现代 Python 版本，**必须同时升级到 PySide6**。这部分工作量不亚于语言迁移本身 |
| 2. 读取 1.21 原版地图 | 可行，但不止改 dat | `level.dat` 其实最简单。真正要改的是 `.mca` 区块格式；用下面的“转换层”可以把改动基本关在读取层 |
| 3. 外部资源添加自定义方块 | 可行，且基础已有 | 现有的未知方块流程、`forcedModel`、问号模型都能直接扩展 |
| 4. 配套导出模组 | 可行，且**要提前做** | 仓库里已有同类工具 `BlockDumper.java`。**原版 1.21 的方块表也得靠它生成**，所以它是需求 2 的前置 |

### 1.2 “扩展读取逻辑就行”对在哪、缺在哪

**对的部分：** 共有 25 个文件直接使用 `Blocks`/`Data` 数组，包括渲染、工具和选择。如果在读取层把新格式转换成它们熟悉的“数字 ID + meta”，这些文件基本不用改。这是本方案的核心。

**需要额外处理的部分：**

| 问题 | 原因 | 处理 |
|---|---|---|
| 高度 -64–320 | 约 15 个文件假设 Y≥0 | 需逐个修，无法完全关在读取层 |
| 贴图路径和模型加载 | 写死 `assets/minecraft/`、`textures/blocks`，模型名默认无命名空间 | 改 `blockmodels.pyx`、`resourceloader.py` |
| 特殊方块逻辑 | 网格器里写着 1.12 的方块名（`flowing_water`、`leaves`、`snow_layer` 等） | 换成 1.21 名称 |
| Qt 和 OpenGL 接入 | `QGLWidget` 在 Qt6 中已删除 | 窗口基类要换，绘制代码可保留 |

**有利的发现：** 1.13 起，栅栏连接、门的上下半、红石线等状态都直接存在世界里。旧网格器中用于推算这些状态的代码（`modelmesh.pyx:266-410`）和 `hiddenstates_*.json` 在新世界中都可以不用，这部分反而变简单了。

### 1.3 必须先知道的安全问题

**不要用旧代码直接打开重要的 1.21 世界。** 读代码发现以下风险：

- **`regionfile.py:134-191`**：区域文件被判定需要修复时，`repair()` 会读 `chunkTag["Level"]`。1.21 区块没有这个键，于是抛异常并进入 `_setOffset(cx, cz, 0)`，等于**把该区块从索引中删除**。这段修复逻辑必须改，否则可能丢区块。
- **`worldfolder.py:214-219`**：枚举区块时遇到空区域文件会直接 `os.unlink` 删除。
- **`worldfolder.py:50-55`**：即使只读打开，缺目录时也会创建目录。
- **`adapter.py:792-805`**：写入 `session.lock` 的方式属于旧协议，与当前游戏的锁机制不一定兼容 **[待验证]**。

**对策：** 开发和测试一律使用世界副本。读取阶段强制只读打开，并修掉上述写入路径。

---

## 2. 总体结构

```text
   NeoForge 客户端（1.21.1）
   ┌───────────────────────┐
   │ 导出模组 mcedit_exporter │  /mcedit_export
   └───────────┬───────────┘
               ▼  方块包：manifest + blocks + block_entities + assets
   MCEdit 2（Python 3 + PySide6）
   ┌───────────────────────────────────────────────┐
   │ 编辑器 UI、工具、渲染（基本沿用，使用 ID + meta）  │
   ├───────────────────────────────────────────────┤
   │ ModernBlockTypeSet：状态字符串 ↔ 虚拟 ID + meta   │ ← 转换层
   ├───────────────────────────────────────────────┤
   │ ModernAnvilAdapter + FormatProfile(DataVersion)  │ ← 新格式读取
   ├───────────────────────────────────────────────┤
   │ RegionFile（补压缩类型、.mcc，修 repair）          │
   └───────────────────────────────────────────────┘
               ▲
   1.21 世界：region/ entities/ poi/ level.dat
```

---

## 3. 需求 1：迁移到 Python 3

### 3.1 目标环境

| 项 | 旧 | 新 |
|---|---|---|
| Python | 2.7 | 3.12 |
| Qt 绑定 | PySide 1（Qt4） | PySide6 |
| Cython | 0.26 | 3.x |
| NumPy | 旧版 | 2.x |
| OpenGL | PyOpenGL + `QGLWidget` | PyOpenGL + `QOpenGLWidget`（兼容配置文件） |
| 打包 | PyInstaller 旧提交 | 当前 PyInstaller |

### 3.2 工作量分布（已统计）

| 类别 | 数量 | 说明 |
|---|---|---|
| Qt 类迁移 | 约 527 处引用 | `QtGui.QWidget` 等移到 `QtWidgets`，可脚本处理 |
| `iteritems`/`itervalues` | 60 处，分布约 30 个文件 | 机械 |
| `basestring` / `unicode` | 29 / 100 行 | 需要看语义 |
| `xrange`、`file()`、`.next()` | 26 / 11 / 12 处 | 机械 |
| 元组参数 | 21 处 | 手工 |
| NumPy `fromstring`/`tostring` | 22 处 | 改 `frombuffer`/`tobytes`，含 NBT 和区域读写 |
| `.ui` 文件 | 33 个 | 用 `pyside6-uic` 重新生成 |
| Cython 模块 | 4 个，约 3.3k 行 | `nbt.pyx` 含 `IF IS_PY2` 分支和 Python 2 C API，改动最大 |
| **bytes/str 语义** | 无法用工具统计 | **最大的隐性风险**，集中在 NBT、资源和 JSON |

其他已知要点：

- `numpy.set_string_function`（`worldeditor.py:41`）在 NumPy 2 中已移除。
- `collections.MutableSequence` 要改成 `collections.abc`；`imp`、`time.clock`、`cStringIO` 都要替换。
- `custom_traceback.py` 依赖 Python 2 内部实现，可删除。
- `QImage.bits()`（`blockmodels.pyx:160`）在 PySide6 的返回类型变了，要配合 Cython 调整。

**可以直接删除，以减少迁移量：**

- `demjson.py`（6283 行，LGPL，只在一处自测中使用）
- `pocket.py`、`java.py`、`minecraft_server.py`（不可达或已失效）
- `sentry.py` 和错误对话框里的 Pastebin 上传（**内含硬编码密钥，不得保留**）
- ObjGraph、Heapy、IPython 控制台等调试工具

### 3.3 方法

1. **`mceditlib` 先行**：无 UI，能用现有测试验证。先修好测试（如 `revisionhistory_test.py` 引用了不存在的 conftest 模块）。
2. **再迁移 `mcedit2`**：先脚本批量替换，再逐个修运行错误。
3. **迁移期间不改行为**：用现有 1.8 测试世界（`test_files/AnvilWorld_1.8`）作基线，新旧行为一致后才进入 1.21 工作。

### 3.4 OpenGL 的取舍

保留旧渲染代码，请求**兼容配置文件**上下文，这样固定管线和显示列表还能用。

- Windows、Linux：通常可行 **[待验证]**。
- macOS：最高只有旧版 2.1 上下文，且 Apple 已弃用，**不作为首发保证平台**。

这是遵照你“渲染用默认的就可以”的决定。代价是将来若必须换核心配置文件，渲染层要重写，这笔账留到以后。

---

## 4. 需求 2：读取 1.21 世界

### 4.1 新增 `ModernAnvilAdapter`

保留旧适配器作回归基线，新格式走新类，由 `findadapter.py:54` 按 `Data.DataVersion` 分派。

| 项 | 旧 | 1.21.1 | 处理 |
|---|---|---|---|
| 版本判断 | `version == 19133` | 同时看 `DataVersion`（3955） | 不支持的版本明确报错，不猜测 |
| 区块结构 | `Level` 包裹 | 字段在根上 | 读取时规范化（4.3） |
| 段数据 | `Blocks/Data/Add` | 调色板 + 紧缩 long 数组 | 向量化解码（4.2） |
| 实体 | 在区块内 | `entities/r.x.z.mca` | 同时读取两个文件后合并 |
| 方块实体 | `TileEntities` | `block_entities` | 映射 |
| 生物群系 | 16×16 字节 | 每段 4×4×4 调色板 | 压成 2D 给渲染用（4.5） |
| 高度图 | `HeightMap` | `Heightmaps.WORLD_SURFACE`（9 位） | 解码并加上最低 Y |
| 维度 | 一层 `*/region` | 含 `dimensions/<ns>/<path>/` | 扩展扫描 |
| 世界信息 | `RandomSeed`、`generatorName` | `WorldGenSettings` | 世界信息面板调整 |
| 压缩 | gzip、zlib | 另有无压缩、LZ4、自定义、`.mcc` | `RegionFile` 补全（LZ4 需新增 Python 依赖） |

### 4.2 虚拟 ID：方案的核心

**问题：** 1.21 没有数字 ID，而旧代码处处用 `(ID, meta)`。

**做法：** 打开世界时，由 `ModernBlockTypeSet` 给“方块名 + 状态”分配**只在本次会话有效**的虚拟 `(ID, meta)`。原作者在 `blockmodels.pyx:104-108` 的注释里也设想过类似思路。

```python
# 段解码（numpy 向量化，不逐块循环 Python）
indices = unpack_palette_indices(data_longs, bits_per_entry)   # 4096 个索引
lut_id, lut_meta = palette_to_virtual(palette_entries)         # 调色板 → 虚拟 ID 查找表
section.Blocks = lut_id[indices].reshape(16, 16, 16)
section.Data   = lut_meta[indices].reshape(16, 16, 16)
```

**分配规则：**

1. **按需分配**：只给世界中实际出现的状态，和方块选择器要显示的方块分配，节省编号。
2. **每个 ID 最多 16 个状态**。状态更多的方块（红石线、音符盒等）占用多个连续 ID。
3. **同一 ID 内属性尽量一致**：`opacity`、`opaqueCube` 等表只按 ID 索引、没有 meta 维度（`blocktypes/__init__.py:401-411`）。所以把这些值相同的状态归入同一个 ID，否则显示会错。
4. **水和岩浆的 meta 等于 `level`**：旧网格器直接把 meta 当液位使用（`modelmesh.pyx:561-582`），这样就不用改它。
5. **绝不落盘**：虚拟 ID 只存在于内存，写回时用原始状态字符串。

**需要扩大的表**（`id_limit` 从 4096 扩到 65536，`Blocks` 本来就是 uint16）：

| 位置 | 内容 |
|---|---|
| `blocktypes/__init__.py:97` | `id_limit` 及相关属性表 |
| `blockmodels.pxd:33`、`blockmodels.pyx:763` | `cookedModelsByID[4096][16]` |
| `rotation.py:16,247` | 旋转表 |
| `operations/analyze.py:62` | `ID \| Data<<12` 打包方式会冲突，要改 |
| `worldscene.py:75` | `renderType` 表 |
| `block_copy.py:21-24`、`block_fill.py:21` | 随 `id_limit` 变化，检查即可 |

### 4.3 区块规范化

读取时把新结构转换成旧代码期望的内存形态，同时**完整保留原始 NBT**，这样下游基本不变，写回时也不丢数据：

- `Entities` / `TileEntities` / `TileTicks` 属性指向对应的新数据。
- 未识别的根键全部保留，包括 `neoforge:attachments`。
- 全空气段不创建 `AnvilSection`（1.21 会把所有段都写进列表）。
- 修改 `inspector/__init__.py:358-414`、`find_replace/nbt.py:543` 里直接访问 `rootTag["Level"]` 的地方。

### 4.4 负 Y 与世界高度

`minHeight`、`maxHeight` 从维度读取，不写死。需要检查和修改的文件：

`adapter.py`、`worldeditor.py`（`:546,:790`）、`block_copy.py`、`schematic.py`、`selection/__init__.py`、`transform.py`、`select.py`、`masklevel.py`、`rendering/selection.py`、`l_system_plugin.py`、`camera.py`、`worldview.py`、`blockmodels.pyx`，以及 `chunkupdate.py`、`raycast.py`、`loadablechunks.py`。

**不采用“内部统一平移 +64”的捷径。** 它虽然省事，但界面坐标、NBT 坐标和原理图都要反复换算，还无法适配自定义高度的维度。

### 4.5 生物群系（只求显示正确）

- 需要 3D→2D：取每列最高非空段对应单元的生物群系，交给现有的 2D 着色逻辑。
- 生物群系温度和降水不再依赖过时的 `biomes.csv`，从 `data/minecraft/worldgen/biome/*.json` 读取（在版本 jar 内），模组的由导出包提供。
- 原始 3D 数据不改动。

### 4.6 光照（只求能看）

旧光照代码（`relight/`）依赖旧高度图和 Y≥0，1.21 世界下**直接停用**。

- 段内有 `SkyLight`/`BlockLight` 就使用。
- 缺失时按顶部全亮规则填充显示值（段缺 `SkyLight` 时，用上方段的底层），避免一片漆黑。

### 4.7 渲染的最小修改（`blockmodels.pyx`、`modelmesh.pyx`）

| 位置 | 改动 |
|---|---|
| `:116`、`:138` | 按命名空间定位 `assets/<ns>/models`、`blockstates` |
| `:446`、`:519` | 模型名已是完整路径（`minecraft:block/stone`），不再拼 `block/` |
| `:599` | 贴图路径按命名空间解析 |
| `:612`、`modelmesh.pyx:174,180` | `textures/block/water_still.png` 等 |
| `:238` | 新世界不加载 `hiddenstates` |
| `resourceloader.py:70,80` | 同样改路径规则，扫描所有命名空间 |
| `modelmesh.pyx:89-162` | 特殊方块名表换成 1.21 名称 |
| `modelmesh.pyx:266-410` | 跳过旧的邻居状态推算 |
| `modelmesh.pyx:464` | 半砖判断用 `type` 属性，而不是 `meta & 8` |
| `minecraftinstall.py:91` | 版本要求改为 1.21.1；识别含 NeoForge 的版本隔离目录 |

**接受的不足：** 无光滑光照和半透明排序，水面是平的，`weight` 随机变体只取第一个。胸箱、告示牌、床、头颅等靠方块实体渲染器显示的方块，走第 5.3 节的回退外观。

### 4.8 验收

- 用你实际的 1.21.1 原版世界副本，只读打开。
- 三个维度都能飞行浏览，方块基本有正确贴图，Y<0 区域可见。
- 检查器能显示方块名和状态，能查看方块实体和实体的 NBT。
- **世界文件的哈希值在浏览前后完全不变。**

---

## 5. 需求 3：未知方块与自定义方块

### 5.1 现有流程（可直接扩展）

| 环节 | 现状 |
|---|---|
| 未知方块来源 | 1.7 FML 表中的方块被标记 `unknown: True`（`adapter.py:619-624`），由 `session.unknownBlocks()` 列出 |
| 手工配置 | Configure Blocks 对话框，存 `defined_blocks.json`（`configure_blocks.py:116`） |
| 应用配置 | `setConfiguredBlocks` 写入 `forcedModel`、`forcedModelTextures` 等（`editorsession.py:717-720`） |
| 模型加载 | `forcedModel` 优先，其次 `resourcePath`/`resourceVariant`（`blockmodels.pyx:212-231`） |
| 兜底 | 没有模型的槽位统一填问号模型（`:763-766`） |

### 5.2 新的解析顺序

遇到调色板里的方块时，依次查找：

```text
1. 用户手工定义（defined_blocks.json）         最高优先
2. 已加载方块包中的该状态
3. 内置原版表中的该状态
4. 方块已知但状态未知 → 用该方块默认状态的属性和外观
5. 完全未知 → unknown=True，问号模型，显示真实 ID
```

**无论哪一步，都保留原始状态字符串。** 未知方块绝不改成空气，这样以后写回才不丢数据。

### 5.3 外部资源包（方块包）

**位置：** `<用户目录>/MCEdit 2 Files/blockpacks/<包名>/`，也支持 zip。

```text
<包名>/
├─ manifest.json          # schemaVersion、mcVersion、dataVersion、loader、模组列表
├─ blocks.json.gz         # 方块和状态
├─ block_entities.json    # 方块实体类型
├─ biomes.json            # 可选：温度、降水、颜色
├─ report.json            # 导出时的失败和回退记录
└─ assets/<ns>/{blockstates,models,textures}/...
```

**资源优先级：** `用户手工定义 > 方块包 > 世界 mods 目录 > 版本 jar`。`ResourceLoader` 的“首个命中优先”机制本身不用改。

**自动匹配：** 打开世界时读取 `level.dat` 的 `fml.LoadingModList`，与方块包的模组列表比较，自动选用最匹配的包；缺失或版本不同就给出提示。

**手写最小定义（不装模组也能用）：** 只有 `internalName` 是必填项。

```json
{ "internalName": "somemod:copper_machine",
  "textures": { "all": "somemod:block/copper_machine" },
  "model": "minecraft:block/cube_all" }
```

### 5.4 Configure Blocks 对话框调整

- 列出当前世界里的未知方块，点击后可配置模型和贴图（沿用现有界面）。
- 去掉 Meta 列，改为“状态”；表格里的 Opacity、Brightness 现在只存不用，要真正生效。
- 模型和贴图列表覆盖所有命名空间。
- 增加“加载方块包”“查看未知方块报告（名称、出现次数）”。

---

## 6. 需求 4：导出模组 `mcedit_exporter`

### 6.1 定位

- NeoForge 1.21.1，**纯客户端**（`@Mod(dist = Dist.CLIENT)`），不影响服务器。
- 本质就是 `BlockDumper.java` 的 1.21 版：沿用它的字段，MCEdit 的 `addJsonBlock` 几乎可以直接读。
- 体量小（预计 1000 行内），只碰注册表和资源，不做渲染，所以版本维护压力远小于重写方案。

### 6.2 使用方式

进入任意世界后执行客户端命令：

```text
/mcedit_export                 # 导出到 <游戏目录>/mcedit_export/<时间戳>/
/mcedit_export install         # 同时复制到 MCEdit 的 blockpacks 目录（路径在配置中设置）
/mcedit_export --vanilla       # 含原版方块，用于生成内置原版表
```

### 6.3 方块导出字段

| 字段 | 来源（1.21.1）**[方法名待实现时确认]** | 旧字段 |
|---|---|---|
| `internalName`、`blockState` | `BuiltInRegistries.BLOCK`、`getPossibleStates()` | 同名 |
| `defaultState` | `defaultBlockState()` | 同名 |
| `displayName`、`unlocalizedName` | `getName()`、`getDescriptionId()` | 同名 |
| `opaqueCube` | `isSolidRender` | 同名 |
| `opacity` | `getLightBlock` | 同名（值域改为 0–15） |
| `brightness` | `getLightEmission` | 同名 |
| `collidable` | 碰撞形状是否为空 | 同名 |
| `hasEntity` | `hasBlockEntity` | 同名 |
| `materialLiquid` | `getFluidState` | 同名 |
| `materialMapColor` | `getMapColor` | 同名 |
| `renderLayer` | `ItemBlockRenderTypes` | 同名 |
| `renderType` | `getRenderShape`，**转换到旧值**：MODEL→3，液体→旧液体值，实体渲染→2 | 同名，取值需换算 |
| `resourcePath`、`resourceVariant` | `BlockModelShaper.stateToModelLocation` | 同名 |
| `tint` | `BlockColors` 与模型 `tintIndex` | 新增 |
| `fallbackTexture` | 粒子贴图 | 新增 |
| `modelSource` | `json`、`fallback`、`baked` | 新增 |
| `creativeTab` | 创造标签页 | 同名 |

状态总量可能很大（原版约 2.6 万 **[记忆值，待实测]**，整合包可能再高几倍），所以用“方块共享字段 + 状态差异”的紧凑结构并压缩；MCEdit 首次读取后建立本地缓存。

### 6.4 模型分级（决定显示效果的关键）

| 级别 | 适用 | 处理 | 显示效果 |
|---|---|---|---|
| T1 | 有标准 JSON 模型 | 导出 blockstates、models、textures，走现有加载流程 | 正确 |
| T2 | 无可用模型（纯方块实体渲染器、代码生成） | `cube_all` + 粒子贴图，利用现有 `forcedModel` | 贴图方块，形状不对 |
| T3 | 自定义模型加载器（`neoforge:obj` 等）、连接纹理 | 将烘焙后的四边形导出为模型 | 接近正确 |
| 兜底 | 以上都失败 | 问号模型，写入 `report.json` | 问号 |

**建议先做 T1 + T2，够用再评估 T3。** T3 需要在 `blockmodels.pyx` 中新增加载路径。无法通过导出解决的是**动画和依赖运行时状态的渲染**，例如 Create 的传动部件只会显示静态或回退外观。这是外部程序的结构性限制，也是这个方案相对游戏内方案的主要取舍。

### 6.5 方块实体导出

```json
{ "id": "create:gearbox",
  "blocks": ["create:gearbox"],
  "hasRenderer": true,
  "defaultNbt": { "...": "..." } }
```

- `hasRenderer` 决定是否走 T2 回退外观。
- `defaultNbt` 通过创建临时实例并保存得到，整个过程 try-catch，失败就留空。它能修好旧版“添加方块实体”只支持 6 个写死名称、其余直接 KeyError 的问题（`inspector/__init__.py:257`）。
- 旧检查器按 `Chest`、`Control` 分派（`entities.py:575`、`inspector/__init__.py:233-250`），要改成 `minecraft:chest` 等新 ID。

### 6.6 资源导出

- 通过 `ResourceManager` 读取，天然支持资源包覆盖。
- 从 blockstates 沿 parent 和贴图引用走一遍，**只导出用得到的文件**，含 `.mcmeta`。
- 单个方块失败不影响整体，错误写入 `report.json`。

### 6.7 合规

导出的贴图和模型属于各模组作者和 Mojang。方块包只供本机使用，**发布 MCEdit 时不得内置**；内置原版表只含方块元数据（旧仓库也是这样做的），贴图运行时从用户自己的版本 jar 读取。

---

## 7. 写回世界（建议作为第二阶段）

你的需求写的是“读取”，但编辑器最终要保存，所以这里单列。**默认安排在读取和自定义方块稳定之后。**

| 要点 | 处理 |
|---|---|
| 段编码 | 虚拟 ID → 调色板 + 紧缩数组；位宽至少 4 位，索引不跨 long；只有一种方块时省略 `data` |
| 光照 | 删除已改区块的光照数据并设 `isLightOn=0`，让游戏重算（已验证：游戏重算并存回 `isLightOn=1`）|
| 高度图 | 删除已改区块的高度图，由游戏重建（已验证：四种高度图都重建了）|
| 实体 | 分别写回 `entities/` 和 `region/` |
| 删除区块 | 同步处理 `region/`、`entities/`、`poi/` |
| 未知数据 | 原样保留 |
| 文件格式 | 补 Sponge `.schem` 导入导出：`.schematic` 只能存数字 ID，无法表达 1.21 方块 |

**撤销系统必须先修**，否则写回等于带病上岗：

- `revisionhistory.py:421`：撤销链实际是星形，保存后多步撤销可能读到错误数据。
- `:656`：同一修订先写后删，旧数据会复活。
- `:659,:736`：墓碑文件只保留最后一条。
- `worldfolder.py:94`：`listAllFiles` 跳过所有 `.mca`，所以 `entities/`、`poi/` 不受版本管理。
- `regionfile.py:255-258`：超过 1MB 的区块只构造异常却不抛出，要改写到 `.mcc`。

**写回验收：** 改过的世界在原版和 NeoForge 专用服务器加载，日志无格式错误；未修改区块的数据不变；模组附件不丢。

---

## 8. 多版本冗余（你之前提的要求）

这个方案的冗余成本很低，因为**版本差异只集中在两处**：

### 8.1 世界格式：`FormatProfile`

```python
class FormatProfile:
    palette_name_key = "Name"          # 26.3 起是 "id"
    palette_props_key = "Properties"   # 26.3 起是 "properties"
    status_key = "Status"              # 26.4 起是 "status"
    biome_resolution = 4               # 26.4 起是 1
    def dimension_folder(self, dim): ...  # 26.1 起目录重排
```

| 版本 | DataVersion | 需要的变化 |
|---|---|---|
| 1.21.1（首发） | 3955 | 本方案 |
| 1.21.2–1.21.11 | 4080–4671 | 预计基本只需放开版本范围和回归（部分方块实体默认值省略）|
| 26.1 | 4786 | 目录重排到 `dimensions/minecraft/*` |
| 26.3 | 5023 | 调色板键改名 |
| 26.4 | 5119+（快照） | 生物群系精确到方块，`Status` 改名 |

### 8.2 导出模组：导出包是边界

- `manifest.schemaVersion` 独立于游戏版本。MCEdit 只认识导出包，不关心它由哪个版本、哪个加载器的模组生成。
- 模组工程按 `exporter-common`（逻辑）+ `exporter-mc1211-neoforge`（入口）分开，将来加 Fabric 或新版本只新增入口和少量差异。
- **Fabric 世界也能用**：世界文件和导出包都与加载器无关。

### 8.3 约束

- 不支持的 DataVersion 一律明确拒绝，不猜测。
- 高度、维度路径、生物群系精度都从配置或世界读取，禁止写死。
- **不提前实现其他版本**，只预留结构。
- 1.13–1.20 不在目标内（1.18 前区块结构不同）。

---

## 9. 第一版的功能影响

虚拟 ID 能让大部分功能继续运转，但这些功能依赖 1.12 语义，需要明确处理：

| 功能 | 第一版（只读阶段） | 第二阶段 |
|---|---|---|
| 浏览、检查器、NBT 查看 | ✅ | ✅ |
| 编辑、撤销、保存 | 禁用 | 随写回开放 |
| 选择、分析 | ✅（分析要改打包方式） | ✅ |
| 填充、替换、笔刷、克隆 | 禁用 | ✅ 基本可沿用 |
| 旋转方块状态 | - | 需把 `rotation.py` 的规则对新名称跑一遍，状态按需分配后要能增量建表 |
| 背包编辑器 | **屏蔽**，只显示 NBT | 需按 `id/count/components` 重做 |
| 玩家面板 | 只读 | 字段更新 |
| 世界信息 | 只读 | 字段更新 |
| 地图面板 | 只读预览（色表需更新） | 后续 |
| 命令方块可视化、`commandblock.py` | **屏蔽**（1.12 语法） | 后续单独评估 |
| 刷怪点叠加层 | 屏蔽 | 后续 |
| `.schematic` 导出 | 屏蔽 | 改用 `.schem` |
| 旧 `.schematic` 导入 | 屏蔽 | 需要 ID→状态转换表 |
| 插件 | 示例中用旧 ID 的需修改 | 同左 |

---

## 10. 里程碑

工作量是粗估（一名全职开发者），未实测，M2 结束时应复核。

| 里程碑 | 内容 | 验收 | 估算 |
|---|---|---|---|
| **M0 基线** | `git init`，归档，建 Python 3.12 环境，整理测试世界副本 | 可重复搭建环境 | 2–3 天 |
| **M1 mceditlib 迁移** | Python 3、Cython 3、NumPy 2，修复测试 | 现有测试通过；1.8 测试世界可读写 | 1.5–2 周 |
| **M2 mcedit2 迁移** | PySide6、`QOpenGLWidget`、旧渲染跑通，删除密钥和废弃模块 | 应用能打开 1.8 测试世界，操作与旧版一致 | 2–3 周 |
| **M3 导出模组 V0** | 原版和方块元数据导出 | 生成内置原版 1.21.1 方块表（已完成：1060 个方块、26684 个状态，全部对照原版 jar 校验） | 3–5 天 |
| **M4 1.21 只读** | 区域、调色板、虚拟 ID、实体、负 Y、修 `repair` | 第 4.8 节验收（可先不要求贴图全对）（已完成，已用开发客户端生成的世界验证；用实际世界副本验收待做）| 2–3 周 |
| **M5 渲染适配** | 命名空间、1.21 名称、液体、着色、回退外观 | 原版世界基本显示正确（已完成：全部 26684 个状态都有模型或回退外观，已用游戏的调试世界验证；已知不足见第 4.7 节）| 1.5–2 周 |
| **M6 自定义方块** | 解析顺序、方块包加载、自动匹配、Configure Blocks | 手写定义能生效；未知方块显示问号且可配置（已完成，已用合成的模组世界和方块包验证；真实模组包在 M7）| 1–2 周 |
| **M7 导出模组完整版** | T1 + T2、方块实体、资源导出、报告 | 一个中型模组包世界：模组方块大部分有贴图（已完成：Create、AE2、Farmer's Delight 共 877 个方块、39202 个状态在开发客户端真实导出，无问号；T3 未做；未用实际整合包和存档验收）| 1–1.5 周 |
| **M8 写回（第二阶段）** | 修撤销，段编码，实体，`.schem`，开放编辑 | 第 7 节验收（代码已完成，开发客户端的 NeoForge 整合服务器加载改过的世界无格式错误，含 Create/AE2 世界；专用服务器未测，背包编辑器和生物群系编辑未做）| 3–4 周 |

**合计：只读可用版（M0–M7）约 10–15 人周；含写回约 13–19 人周。**

**顺序说明：** M3 排在 M4 之前，和你列的需求顺序略有不同，因为原版方块表依赖它。M6、M7 可以交叉进行。

**建议的第一个检查点是 M4 之后：** 那时能看到 1.21 世界。如果效果和速度满足预期再继续投入。

---

## 11. 需要你确认

| 编号 | 问题 | 默认方案 |
|---|---|---|
| Q1 | **1.21 世界的写回是否进入第一版？** | **已确认（2026-11-05）：** 不进入。先做只读 + 自定义方块（M0–M7），写回作为第二阶段 |
| Q2 | 主要在哪些系统使用？ | Windows 优先，Linux 尽量；macOS 不保证（OpenGL 限制） |
| Q3 | T3（烘焙模型导出）要不要做？ | 先不做，等 M7 看实际效果 |
| Q4 | 是否继续保留 1.12 及以下世界支持？ | 迁移期保留作回归，之后不再承诺 |

---

## 12. 风险

| 风险 | 影响 | 应对 |
|---|---|---|
| bytes/str 隐性错误 | 高 | `mceditlib` 先迁移；NBT 往返测试；基线世界比对 |
| 旧逻辑误伤新世界（`repair`、删除空文件） | **高，可能丢数据** | 第 1.3 节；只用副本开发；只读阶段拦截写入 |
| Qt6 兼容配置下固定管线异常 | 中 | M2 早期先做最小验证；必要时降级或改用 ANGLE 之外的驱动方案 |
| 单线程加载和网格，大世界卡顿 | 中 | 原架构如此，第一版接受；解码用 numpy/Cython |
| 整合包状态量过大 | 中 | 按需分配；缓存；`id_limit` 65536 的压力测试 |
| 模组方块显示不完美 | 中，**已接受的取舍** | T1/T2/T3 分级 + 报告 |
| LZ4、`.mcc` 少见格式 | 低 | 单独测试；未安装 LZ4 依赖时明确报错 |
| 旧代码隐藏缺陷多，测试薄 | 中 | 边迁移边补测试；优先覆盖数据路径 |

---

**第三方代码约束（Amulet）：** 不得复制或移植 Amulet 系列仓库的代码和数据，也不把它们作为依赖。核查结果（2026-11-05，非法律意见）：

| 仓库 | 当前许可 |
|---|---|
| Amulet-Map-Editor、Amulet-Core、PyMCTranslate | “All rights reserved”，使用需购买许可 |
| Amulet-NBT、Minecraft-Model-Reader | Amulet Team License 1.0.0：限非商业，且含竞争条款，免费产品同样算竞争 |
| Amulet-Anvil、Amulet-Game、Amulet-Resource-Pack | 未找到 LICENSE 文件，不代表可以使用 |

格式实现以 Minecraft Wiki 和实际世界文件为依据，保持本项目 BSD 代码干净。

---

## 13. 与 `REMAKE_PLAN.md` 的关系

`REMAKE_PLAN.md` 是此前的游戏内模组重写方案，现已不是当前路线，仅作参考保留。其中的旧代码缺陷清单（第 1.4 节）、数据格式备忘（第 7 节）和版本变更表（第 8.1 节）仍然适用。
