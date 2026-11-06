# MCEdit 重置企划：面向 Minecraft 1.21.1 NeoForge（预留多版本兼容）

> **已被替代：** 当前路线见 [PLAN_PY3_1.21.md](PLAN_PY3_1.21.md)（在旧代码上升级到 Python 3，配合导出模组）。本文的游戏内模组重写方案不再是当前路线，其中的缺陷清单、格式备忘和版本变更表仍可参考。

| 项 | 内容 |
|---|---|
| 文档状态 | 草案 v1，待确认第 0.3 节的决策 |
| 编写日期 | 2026-11-05 |
| 首发目标 | Minecraft 1.21.1 + NeoForge 21.1.x，Java 21 |
| 设计约束 | 主实现只做 1.21.1 NeoForge；架构上隔离版本差异，新增版本只新增适配层 |
| 工作代号 | MCEdit Reborn（`mcedit_reborn`），正式名称待定，见 D2 |

标注约定：

- **[已核实]**：本次已对照 NeoForge 1.21.1 源码、官方文档、Maven 或 Minecraft Wiki 确认。
- **[待验证]**：来自经验判断或尚未实测，必须在 Phase 0 的预研里验证后才能依赖。
- 旧代码行号（如 `adapter.py:217`）来自静态阅读。旧代码依赖 Python 2 和已退役的 Qt4 工具链，本次没有运行过，所以文中旧代码的缺陷都是“读代码得出”，不是运行复现。

---

## 0. 一页摘要

### 0.1 结论

1. **不做移植，做重置。** 旧项目的问题不只是 Python 2 这类语言债。它的数据模型（数字 ID + 4 位 meta）、世界格式（`Level` 标签、`Blocks/Data/Add` 数组、0–256 高度）、方块模型加载和渲染管线（OpenGL 1.3 固定管线）都建立在 1.12 及以前的世界观上，与 1.21.1 全面冲突。可以继承的是**设计思想和交互语义**，不是代码。
2. **形态上做成运行在游戏内的 NeoForge 模组（Java 21）。** “完美”的最大难点是模组内容：模组方块的动态模型、方块实体渲染器、自定义数据、注册表内容。只有在游戏进程内才能直接使用这些能力；外部编辑器永远只能近似。详见第 3 节。
3. **用“端口与适配器”架构预留多版本。** 业务逻辑放在不依赖 Minecraft 的 `core`，版本差异全部收口到 `platform-<mc版本>-<加载器>` 适配模块。第 8 节已按真实版本差异逐项演算：目前查阅到的 1.21.2 到 26.4 破坏性变更都能落在适配层内（部分迁移文档未读完，见附录 B）。
4. **先做数据安全，再做功能。** 世界编辑器一旦写坏存档，功能再多也没用。第一条硬指标是：任何操作都可撤销，崩溃后可恢复，未知数据不丢失。
5. **预计工作量约 32–41 人周**（一名熟悉 NeoForge 的全职开发者的粗估），分 10 个阶段，每阶段都有可验收的产出。这是规划量级，不是承诺，Phase 0 结束后应复核一次。

### 0.2 用户将得到什么

- 在世界选择界面点“用 MCEdit 打开”，或在游戏内按一个键，进入编辑器模式。
- 保留 MCEdit 2 的完整工作流：自由相机、框选、笔刷、移动/克隆、填充替换、NBT 编辑、玩家/世界/地图面板、历史记录、原理图库、多视图。
- 所有模组方块都用真实模型和真实渲染器显示，方块选择器自动列出所有模组内容，**不再需要 Configure Blocks 这类手工配置**。
- 单人世界和专用服务器（权限控制下）都能使用。
- 插件可以用 Java 注册新工具、笔刷、生成器和检查器。

### 0.3 需要你确认的决策

| 编号 | 决策 | 企划采用的默认值 | 改变默认值的影响 |
|---|---|---|---|
| **D1** | 产品形态 | **游戏内 NeoForge 模组**，保留将来做离线 CLI 的路径 | 若坚持外部桌面程序，第 5.3、5.9、5.10 节和路线图要重写，模组内容也无法做到完美显示（见第 3 节） |
| D2 | 对外名称 | 独立新名称，注明“基于 MCEdit 2” | BSD 第三条限制用原项目名为衍生品背书；如想沿用 MCEdit 之名，建议先联系原作者 |
| D3 | 1.0 是否包含专用服务器 | **包含**，架构从第一天就走服务器权威 | 只做单人可省约 2–3 人周，但这个架构本身不能省 |
| D4 | UI 技术 | **Dear ImGui（imgui-java）**，需 Phase 0 验证通过 | 备选是自研控件库，后续每个 GUI 破坏性版本都要付迁移成本（见 5.10） |
| D5 | 许可证 | 沿用 BSD-3-Clause，保留原版权声明 | 无 |

---

## 1. 现状审计

### 1.1 项目规模

| 模块 | 规模 | 内容 |
|---|---|---|
| `src/mceditlib/` | 约 18.6k 行 Python + Cython | 世界读写、NBT、撤销存储、选择代数、变换、光照 |
| `src/mcedit2/` | 约 31.1k 行 Python + Cython，33 个 `.ui` | PySide/Qt4 界面、OpenGL 渲染、工具、面板、插件系统 |
| `src/plugins/` | 约 1.1k 行 | 示例插件（city、hilbert、koch、storagedrawers 等） |
| `tests/` | 19 个文件 | 几乎只覆盖 mceditlib，多数偏冒烟 |
| 数据文件 | 1.12 方块/物品/ID 转储 | `minecraft_raw_1_12.json` 等，最新只到 1.12 |

上游最后一次发布是 2018-08 的 2.0.0-beta14，最后一次推送是 2022-05，方块数据停留在 1.12。当前目录也不是 git 仓库。

### 1.2 为什么无法在 1.21.1 上工作

问题在世界观层面，不是个别 bug：

| 层面 | 旧代码假设 | 1.21.1 实际情况 | 证据 |
|---|---|---|---|
| 区块根结构 | `Level` 包裹 | 1.18 起无 `Level`，字段在根上 | `adapter.py:217,249` |
| 方块存储 | `Blocks`+`Data`+`Add` 字节数组 | 按段调色板 + 紧缩 long 数组 | `adapter.py:135-147` |
| 方块标识 | 12 位 ID + 4 位 meta，`id_limit=4096` | 字符串 ID + 属性，没有存档内数字 ID | `blocktypes/__init__.py:97,134-138` |
| 世界高度 | 0–256 | -64–320，且可由维度类型改变 | `adapter.py:292,409-410` |
| 实体存储 | 随区块存放 | 独立的 `entities/` 区域文件 | `adapter.py:317`、`worldfolder.py:94` |
| POI | 不处理 | 独立的 `poi/` 区域文件，删区块时必须同步 | 全库无相关实现 |
| 生物群系 | 16×16 字节数组 | 每段 4×4×4 调色板，字符串 ID | `adapter.py:218,329` |
| 高度图 | 单一 `HeightMap` 整型数组 | 多种类型，紧缩 long 数组 | `adapter.py:232,334` |
| 区域压缩 | 仅 gzip、zlib | 还有无压缩、LZ4、自定义、`.mcc` 外置 | `regionfile.py:43-44,242` |
| 维度路径 | 一层 `*/region` 扫描 | `dimensions/<命名空间>/<路径>/region` 是三层 | `worldfolder.py:101-110` |
| 物品格式 | `id/Damage/Count/tag` | `id/count/components`（1.20.5 起数据组件） | `entities.py:133` |
| 实体 ID | `Zombie`、`Chest`、`Control` | `minecraft:zombie` 等命名空间 ID | `entities.py:432-444,575-582` |
| 模组支持 | 仅 1.7 的 `FML.ItemData` | 世界存字符串 ID，`level.dat` 的 `fml.LoadingModList` 只记模组列表 | `adapter.py:581` |
| 资源路径 | `textures/blocks`、无命名空间解析 | `textures/block`、模组命名空间 | `blockmodels.pyx:116,138,599` |
| 渲染 | OpenGL 1.3 固定管线、显示列表、无着色器 | 不可直接复用 | `qglcontext.py:19` |
| 命令解析 | 9 条 1.12 语法命令 | Brigadier，语法全变 | `util/commandblock.py` |

运行环境同样全部失效：Python 2.7、PySide 1/Qt4、`cython==0.26`、PyInstaller 旧提交、Travis/AppVeyor 配置。

### 1.3 值得继承的设计（概念，不是代码）

| 设计 | 位置 | 继承方式 |
|---|---|---|
| 选择代数：`& \| + - ~`，以 `box_mask` 为统一契约的惰性树 | `mceditlib/selection/__init__.py` | Java 重写，修复已知缺陷（第 1.4 节） |
| 基于文件的写时复制撤销链 | `revisionhistory.py` | 取其思想，重新设计（第 5.5 节） |
| 生成器式长任务与进度上报 | `showprogress.py`、`operations/` | 变为可取消的服务器任务 |
| 分段切片的块复制 | `block_copy.py` | 重写为调色板感知版本 |
| 惰性变换视图（目标坐标反算源坐标） | `transform.py` | 保留算法，补上实体、方块实体和生物群系 |
| 基于状态字典的旋转规则 | `blocktypes/rotation.py` | 只作为 X/Z 轴旋转的回退规则 |
| 螺旋形区块加载顺序、图层模型、脏标记传播 | `chunkloader.py`、`layers.py` | 用于自有渲染器和小地图 |
| BoxHandle 拖拽语义、边缘选背面的判定 | `boxhandle.py`、`selection.py:413` | 原样保留，这是 MCEdit 手感的核心 |
| 声明式插件选项 DSL | `plugins/command.py:160` | 改写成 Java 构建器 |
| 射线穿过实心块直到遇到空气 | `raycast.py:79-87` | 作为可切换选项保留 |

### 1.4 审计中发现、新版本不得继承的缺陷

静态阅读得出，用来防止把旧问题带进新设计，也是新版本测试用例的来源：

**数据安全类（最严重）**

- 撤销链实际是星形结构：`revisionhistory.py:421` 取 `nodes[currentIndex - direction]`，这个位置当时总是根节点。文档（`:87`）画的是链。**保存后连续撤销超过一步可能读到错误数据。**
- 同一修订内先写后删会让父版本数据复活（`:656`）；墓碑文件以 `"w"` 打开，只保留最后一条（`:659,:736`）。
- 没有崩溃恢复，`resume=True` 直接抛 `NotImplementedError`（`:172`）；保存直接写入世界目录，不是原子操作。
- `regionfile.py:255-258` 对超过 1MB 的区块只**构造**了异常对象却没有 `raise`，随后继续写入，而扇区数字段只有 1 字节。
- `adapter.py:1042` 的 `AnvilPlayerRef(playerTag, self)` 与构造函数 `:1394` 的参数顺序相反。
- `worldeditor.py:546` 的 `containsPoint` 仍把高度上限写死为 127。

**功能类**

- 选择代数：`CombinationBox.__contains__` 必定抛异常（`selection/__init__.py:284`）；差集的 `sectionPositions` 用集合相减，部分重叠的段会被漏掉（`:345`）。
- `block_copy.py:218` 在 `entities=False` 时仍复制方块实体；`block_fill.py:150-155` 不移除被覆盖位置的方块实体。
- `transform.py:158-160` 的变换包装器丢弃实体、方块实体和生物群系；旋转表没有镜像；`rotation.py:175` 覆盖循环参数，影响后续方块。
- 插件系统：工具插件永远不会出现（`editortools/__init__.py:30` 返回固定元组）；笔刷形状和模式没有注册入口，文档却写了。
- NBT 查找替换：数值永远匹配不上（`find_replace/nbt.py:441`），实体结果的替换/删除调用不存在的 `getEntity`（`:82`）。
- 射线全部按整格立方体处理，没有方块形状。

**未完成的 UI**（新版本要么真正实现，要么明确放弃，详见第 6 节）

New World、Save As、Backup、Repair、Create Chunks、Paste Blocks/Entities、Find Blocks、Find Items、命令文本查找、Iso/4-up 视图、日志查看器、待处理导入列表等。

**安全与合规类**

- `sentry.py:13` 硬编码 Sentry DSN（含密钥），错误上报**默认开启**；`error_dialog.py:116` 硬编码 Pastebin API key。**两者都不得迁移，应视为已泄露。**
- 启动时可能在弹窗前静默下载 Mojang 客户端 jar（`minecraftinstall.py:206-218`）。
- 许可证表述矛盾：`LICENSE.md` 是 BSD-3，而 `setup_*.py` 写 MIT。
- `util/demjson.py` 是 LGPLv3+，且实际只在命令解析的自测中使用。
- `assets_raw/playerskins/` 下的皮肤没有授权信息。

### 1.5 旧版本真实的功能边界

这张清单决定了“功能对等”的基线。注意以下**不是**旧版实际具备的能力，不应被当作继承项：

- 虽然 CHANGES 提到多对象导入，但实际同一时间只能有一个待放置导入。
- 视图里只有 2D 切面、俯视、相机和小地图可用；Iso、4-up 已注释掉，读代码来看也无法导入。
- 没有天花板（ceiling）开关，没有光照总开关，没有平滑光照和 AO。
- Pocket、Classic、Indev 代码都已不可达。
- 笔刷形状/模式不可扩展。

---

## 2. 目标与“完美使用”的定义

“完美”需要可验证，否则无法验收。本企划定义如下：

### 2.1 数据完整性（一票否决）

| 指标 | 标准 |
|---|---|
| 未知数据保留 | 只改方块的操作前后，区块根上的未知键、`neoforge:attachments`、`neoforge:aux_lights`、模组方块实体 NBT、实体数据逐字节语义相等 |
| 撤销精确性 | 随机操作序列（含方块实体、实体、生物群系）执行后全部撤销，世界与初始状态语义相等 |
| 崩溃恢复 | 操作中途强杀进程，下次打开世界能检测到并恢复到操作前或操作后的一致状态 |
| 不产生意外地形 | 编辑只触及已存在区块时，不生成新区块（除非用户明确要求） |
| 格式兼容 | 工具写出的所有文件被原版/NeoForge 专用服务器无警告加载 |

### 2.2 模组兼容

| 指标 | 标准 |
|---|---|
| 内容发现 | 所有注册的方块、物品、实体、生物群系、维度自动可用，零配置 |
| 显示 | 静态模型、ModelData、方块实体渲染器按模组实现显示 |
| 兼容矩阵 | 第 10.4 节列出的模组组合通过冒烟测试；含 Sodium、Iris |
| 失败隔离 | 单个模组方块实体保存/加载抛异常时，操作继续，问题写入报告，不崩溃 |

### 2.3 性能（目标值，Phase 0 校准）

| 场景 | 目标 |
|---|---|
| 填充 100 万方块（已加载，快速路径） | ≤ 5 秒，期间服务器 ≥ 15 TPS |
| 选择框 + 覆盖层绘制 | 每帧 ≤ 2 ms |
| 编辑器 UI 整体 | 每帧 ≤ 3 ms，且不造成持续内存分配抖动 |
| 撤销 100 万方块操作 | ≤ 操作耗时的 1.5 倍 |
| 全部长任务 | 随时可取消，取消后世界一致 |

### 2.4 明确不做（1.0 范围外）

- 基岩版和 Pocket 格式。
- 1.21.1 以前的存档直接编辑。旧档应先由游戏升级；旧原理图的导入是支持的。
- 与 Axiom、WorldEdit 协议互通。
- 实时多人协同的冲突合并。1.0 只做到按用户隔离历史、支持锁区与冲突告警。
- 把旧 Python 插件自动转换为新版本。

---

## 3. 形态选型

| 维度 | A. 外部桌面程序（Python 3/PySide6 或类似） | **B. 游戏内 NeoForge 模组（采用）** | C. 外部程序 + 伴生模组 |
|---|---|---|---|
| 模组方块显示 | 只能解析 JSON 模型，动态模型和方块实体渲染器无法再现 | **完全真实** | 依赖导出，仍无法再现动态渲染 |
| 模组数据理解 | 只有原始 NBT | **可调用模组代码** | 部分 |
| 方块行为（旋转、连接） | 需要外部规则表 | **`BlockState#rotate/mirror` 等由方块自身实现** | 需导出 |
| 光照、高度图、POI | 要自己重写并追版本 | **用引擎** | 自己实现 |
| 可复用旧代码 | 可复用部分概念与少量算法，但核心都得重写 | 只复用概念 | 少量 |
| 编辑不开游戏 | **可以** | 需启动游戏（可后续提供 CLI） | 可以 |
| 超大世界全局操作 | **流畅** | 需分批和流式设计 | 流畅 |
| 专用服务器编辑 | 只能停服 | **可在线** | 可部分 |
| 版本维护 | 要追存档格式和模型格式 | 要追游戏 API | 两者都要追 |

**采用 B 的理由：**

1. 你的目标是“在 NeoForge 平台上完美使用”，而外部方案在“动态模型和方块实体渲染器”这一项上结构性地做不到完美。
2. “无法编辑未加载的世界”这个 B 的主要短板，可以用分批加载和冷数据通道补上（第 5.3 节）。
3. 多版本维护成本可以通过适配层控制（第 4、8 节）。C 方案反而要维护两套需要同步的东西。

**留出的后路：** `core` 不依赖 Minecraft，将来可以基于同一份核心做离线 CLI 或桌面工具，无需推翻架构。

**现有方案参考（本次未深入调研）：** WorldEdit 已支持 NeoForge 1.21.1，但以命令为主；Axiom 公开信息显示主要面向 Fabric，NeoForge 是否原生支持我没能确认；Axion 是另一款开源工具，已确认其 NeoForge 版面向 1.21.6–1.21.11。Phase 0 建议花半天实际体验，把差异化做清楚：**以 MCEdit 式的完整编辑工作流、1.21.1 原生支持和模组数据安全为定位。**

---

## 4. 总体架构与版本冗余设计

### 4.1 分层

```text
┌──────────────────────────────────────────────────────────────┐
│ plugins（第三方，依赖 mcedit-api）                            │
├──────────────────────────────────────────────────────────────┤
│ mcedit-ui        ImGui 面板、对话框、工具选项（不碰 MC）      │
│ mcedit-editor    会话、工具、命令、撤销 UI 模型（不碰 MC）    │
├──────────────────────────────────────────────────────────────┤
│ mcedit-engine    操作管线、历史、任务调度、格式编解码         │
│ mcedit-core      几何、选择代数、Id、状态调色板、NBT 模型     │
├───────────────────── 端口（SPI）mcedit-platform-api ──────────┤
│ WorldPort  BlockPort  EntityPort  NetPort  RenderPort         │
│ CameraPort InputPort  ResourcePort FormatProfile ...          │
├──────────────────────────────────────────────────────────────┤
│ platform-mc1211-common    Mojang 映射下的 1.21.1 实现         │
│ platform-mc1211-neoforge  入口、事件、Payload、mods.toml      │
│ （未来）platform-mc12111-neoforge / platform-mc261-neoforge   │
│ （未来）platform-mc1211-fabric                                │
└──────────────────────────────────────────────────────────────┘
```

**依赖铁律（由构建强制，不靠自觉）：**

- `core`、`engine`、`editor`、`ui`、`platform-api` 的编译类路径上**没有** Minecraft 和 NeoForge。用 Gradle 依赖约束加 ArchUnit 测试双重把关。
- 只有 `platform-*` 能 `import net.minecraft.*` 或 `net.neoforged.*`。
- `ui` 只依赖 imgui-java 和 `editor`，所以 GUI API 的版本变动碰不到它。

### 4.2 版本冗余的十条设计规则

| # | 规则 | 防的是哪次真实变更 |
|---|---|---|
| R1 | 核心用自有 `Id(namespace, path)`，不用 `ResourceLocation` | 1.21.11 改名为 `Identifier` |
| R2 | 核心用自有 `NbtNode` 树，边界转换 | 1.21.5 `CompoundTag` 访问器重写、`Tag` sealed |
| R3 | 方块实体/实体数据经 `BlockEntityPort`/`EntityPort` 读写，核心只见 `NbtNode` | 1.21.6 改为 `ValueInput/ValueOutput` |
| R4 | 核心绘制只产出顶点数据和图元描述，由 `RenderPort` 提交 | 1.21.2 着色器、1.21.5 `RenderPipeline`、1.21.11 `RenderTypes`、26.x Vulkan |
| R5 | UI 不使用 `Screen`/`GuiGraphics` 控件；ImGui 绘制数据经 `UiRenderBackend` 输出 | 1.21.2、1.21.6 GUI 重写，26.1 `GuiGraphicsExtractor` |
| R6 | 输入统一成自有 `InputEvent` | 1.21.9 `MouseButtonEvent/KeyEvent`、`KeyMapping.Category` |
| R7 | 存档结构差异集中在 `FormatProfile`，按 DataVersion 选择 | 26.1 目录重排，26.3 调色板键改名，26.4 生物群系精度和 `status` 改名 |
| R8 | 世界高度、生物群系精度、维度路径一律查询，禁止硬编码 | 旧版就栽在这里；还有 26.4 的按方块生物群系 |
| R9 | 所有持久化文件和网络协议都带版本号，原理图带 `DataVersion` | 跨版本打开历史和剪贴板 |
| R10 | 每个适配层都必须通过同一套 TCK 契约测试（第 10.3 节） | 防止适配层悄悄行为漂移 |

### 4.3 端口（SPI）清单

| 端口 | 职责 | 1.21.1 实现要点 |
|---|---|---|
| `WorldPort` | 维度列表、高度范围、区块存在性/加载、段读写、提交变更 | `ServerLevel`、`LevelChunkSection`、票据 |
| `ColdChunkPort` | 不经加载直接读写区块/实体/POI 的 NBT | `ChunkStorage#read/write/flushWorker` **[已核实存在]** |
| `BlockPort` | 枚举方块与状态、解析、属性、形状、旋转、镜像、标签 | `BuiltInRegistries.BLOCK`、`BlockState` |
| `BlockEntityPort` | 读写方块实体 NBT、创建、定位 | 版本差异点（R3） |
| `EntityPort` | 列举、读写、生成、移除、变换实体 | 版本差异点（R3） |
| `ItemPort` | 物品枚举、图标、组件校验 | `DataComponentPatch` |
| `BiomePort` | 生物群系枚举、读写、同步 | `FillBiomeCommand` 同类逻辑、`ClientboundChunksBiomesPacket` **[已核实存在]** |
| `PlayerPort` | 玩家数据读写、在线/离线分流 | `playerdata/*.dat` |
| `LevelDataPort` | `level.dat`、游戏规则、时间、出生点、Tick 冻结 | `ServerTickRateManager` **[已核实存在]** |
| `NetPort` | 版本化 Payload、分片传输 | `RegisterPayloadHandlersEvent` |
| `PermissionPort` | 权限判定 | OP 等级 + `PermissionAPI` |
| `RenderPort` | 覆盖层图元、离屏渲染、方块预览网格 | `RenderLevelStageEvent`、`BlockRenderDispatcher` |
| `CameraPort` | 相机位置、投影、拾取射线 | 见 5.9 |
| `InputPort` | 键鼠捕获与转发 | GLFW 回调链 |
| `ResourcePort` | 方块/物品图标、语言文件、纹理句柄 | `ResourceManager` |
| `DataFixPort` | 旧 DataVersion 数据升级 | `DataFixers`、`BlockStateData` **[已核实存在]** |
| `FormatProfile` | 存档键名、目录规则、物品格式 | 纯数据，位于 `engine`，可离线使用 |

### 4.4 工程结构

```text
mcedit-reborn/
├─ build-logic/                       # 约定插件：Java 版本、依赖约束、发布
├─ mcedit-core/                       # 无 MC 依赖
├─ mcedit-engine/
├─ mcedit-platform-api/
├─ mcedit-editor/
├─ mcedit-ui/
├─ mcedit-api/                        # 对外插件 API（语义化版本）
├─ platform/
│  ├─ mc1211-common/                  # MDG 原版模式（无加载器）[待验证]
│  └─ mc1211-neoforge/                # 最终产物 jar
├─ tck/                               # 适配层契约测试
├─ testworlds/                        # 黄金世界（自行生成，不含第三方资产）
├─ legacy/mcedit2/                    # 旧代码原样归档，只读
└─ docs/
```

### 4.5 构建基线

| 项 | 取值 |
|---|---|
| JDK | 21 **[已核实]** |
| NeoForge | 基线 **21.1.265**（当前 21.1 最新）**[已核实]**；最低不得低于 21.1.229，后者修复了服务端可被大集合长度触发内存溢出的漏洞 **[已核实]** |
| 构建插件 | ModDevGradle（`net.neoforged.moddev`），当前最新 2.0.148 **[已核实]**；Gradle 版本跟随官方 1.21.1 MDK |
| 映射 | Mojang 官方映射 + Parchment 1.21.1（具体版本以 parchmentmc.org 为准）|
| UI 库 | imgui-java 1.92.7.1（2026-07 发布）**[已核实版本存在，集成待验证]** |
| 模组声明 | `META-INF/neoforge.mods.toml`；modId 只能用小写字母、数字、下划线，长度 2–64 **[已核实]** |
| 依赖打包 | MDG Jar-in-Jar |
| 备注 | 21.1.180 以后 `@EventBusSubscriber` 的总线参数行为有变化 **[文档已提及]**，新项目直接按新版写 |

> 关于多版本工具：考察过 Stonecutter 式预处理器。它适合版本差异小的模组，但会让条件编译注释散布在业务代码里，与 4.1 的隔离目标相反。**采用独立适配模块**，只有在相邻版本适配层高度相似时，才在适配层内部局部使用预处理器。

---

## 5. 子系统详细设计

### 5.1 编辑器模式与会话生命周期

**进入方式：**

1. 世界选择界面新增“用 MCEdit 打开”按钮（`ScreenEvent.Init.Post`）。
2. 游戏内按键（默认未绑定冲突键，可在控制设置中修改）。
3. 命令 `/mcedit open`。

**进入时：**

- 权限校验 → 建立 `EditorSession`（每玩家每维度状态）→ 可选冻结世界 Tick → 切换相机模式 → 打开 UI。
- 把玩家原位置、游戏模式、飞行状态写入**持久化附件**（`AttachmentType`），防止崩溃后玩家卡在编辑状态。

**世界冻结：** 默认在单人下启用 `ServerTickRateManager` 冻结，避免红石、流体、实体在编辑中变化，也让撤销更干净。提供“活体编辑”开关。专用服务器默认不冻结，也不应由单个编辑者随意冻结。

**“保存”语义的重大变化：**

旧版 MCEdit 的编辑在 Save 之前都不落入世界。游戏内编辑是**即时生效**的，由引擎按自身节奏落盘。必须在 UX 上明确，并提供等价的安全感：

| 旧版概念 | 新版对应 |
|---|---|
| Save | 强制落盘（等价 `save-all flush`），并标记一个“保存点” |
| Close 时选择 Discard | “放弃本次会话”：通过历史回滚到会话起点 |
| 撤销目录随关闭删除 | 历史可跨会话保留，受磁盘配额约束 |
| 无 | 首次编辑前可选自动备份（默认询问；优先用原版的世界备份能力 **[待验证]**） |

### 5.2 核心数据模型（`mcedit-core`）

```java
record Id(String namespace, String path) { }

// 会话内稳定的状态引用，整数只在一次会话内有效，绝不落盘
final class StatePalette {
    int intern(BlockStateSpec spec);       // Id + 有序属性
    BlockStateSpec spec(int stateRef);
}

// 不认识的方块也能表达：缺失模组的状态保持原样
record BlockStateSpec(Id block, SortedMap<String, String> properties) { }

interface Selection {
    BlockBox bounds();
    boolean contains(int x, int y, int z);
    void sectionMask(int cx, int cy, int cz, long[] out64); // 4096 位掩码
    Iterable<SectionPos> sections();
    SelectionDescriptor describe();                         // 可序列化，用于网络和历史
}
```

设计要点：

- **未知状态是一等公民。** 原版加载时会把不认识的方块降级，这是模组被移除后丢方块的常见原因。在冷数据通道和原理图里，`BlockStateSpec` 原样保留未知方块。
- **`NbtNode` 自有实现**，支持全部 12 种标签，SNBT 读写，并正确处理 Java 修改版 UTF-8（旧 `nbt.pyx:715` 按严格 UTF-8 处理，碰到增补字符会出错），`long[]`/`int[]` 按有符号处理。
- **掩码是 `long[64]`**，即 4096 位，运算天然高效，并替代旧版的 NumPy 布尔数组。

### 5.3 世界访问：热、温、冷三层

这是形态 B 的核心难点。

| 层 | 定义 | 用途 | 机制 |
|---|---|---|---|
| **热** | 已加载的 `LevelChunk` | 绝大多数编辑 | 直接操作 |
| **温** | 磁盘上存在但未加载 | 超出加载范围的编辑 | 滑动窗口：加票据 → 等加载 → 编辑 → 释放，同时最多 N 个 |
| **冷** | 直接读写 NBT 不进入引擎 | 整区块操作、全图分析、地图瓦片 | `ChunkStorage#read/write/flushWorker` |

**核心决策：方块编辑默认走“热/温”，冷通道只做整区块级操作和只读分析。**

理由：经引擎加载能保证 NeoForge 的 `ChunkDataEvent.Load/Save`、附件和模组逻辑都被触发，最稳妥。冷通道直接改方块虽然快，但等于自己追格式；只作为 1.0 之后的可选加速。

**必须预研验证 [待验证]：**

- 只存在于磁盘的 `FULL` 区块，在邻居未生成时加载，会不会引发新地形生成。若有副作用，就在世界边缘改用冷通道处理或明确提示。
- 票据类型和半径如何设置，才能不触发实体 Tick 且仍能安全写入。
- 判断“磁盘上存在”的最低成本方法。

**冷通道的硬性约定：**

- 删除区块时**同时**处理 `region/`、`entities/`、`poi/`。
- 操作前确保目标未加载，并先 `flushWorker`；写回经 `ChunkStorage#write` 保证与 IO 工作线程排序一致。
- 支持读取全部压缩类型：gzip(1)、zlib(2)、无压缩(3)、LZ4(4)、自定义(127)、`.mcc` 外置（类型值 +128）。**[已核实]**
- 写入不主动处理的键一律原样保留。

### 5.4 编辑引擎：操作管线

**所有编辑走同一条路径，单人也不例外：**

```text
工具（客户端）
  └─ 产出 EditIntent（意图：选择描述 + 操作 + 参数）
       └─ Payload → 服务器校验（权限 / 配额 / 范围 / 载荷上限）
            └─ JobQueue 按用户排队
                 └─ 规划：分解为按段任务，计算涉及区块
                      └─ 前镜像写入历史 WAL 并 fsync         ← 先写日志
                           └─ 按预算逐 Tick 应用到区块
                                └─ 收尾：光照 / 高度图 / POI / 客户端同步
                                     └─ 历史提交 → 进度与结果回传
```

发送**意图**而不是方块数据，所以体量很小，也符合客户端到服务器单包 32 KiB 的限制 **[已核实]**。

**应用策略（关键取舍）：**

| 路径 | 机制 | 速度 | 使用场景 |
|---|---|---|---|
| **快速路径** | 直接 `LevelChunkSection#setBlockState`，批量更新高度图 | 高 | 无方块实体的普通方块替换 |
| **安全路径** | `Level#setBlock` 配合可选标志 | 低 | 涉及方块实体、模组有 `onPlace/onRemove` 逻辑的方块 |

- 默认“自动”：涉及方块实体或用户勾选“物理更新”时走安全路径，其余走快速路径。
- 模组多方块结构用快速路径可能状态不一致，所以提供“模组方块一律安全路径”开关和方块标签白名单（`#mcedit_reborn:safe_path`）。
- 邻居更新默认关闭，这是 MCEdit 的传统语义（放沙子不掉、放水不流）。可以在操作级打开。

**收尾步骤（缺一就是 bug）：**

| 步骤 | 处理 |
|---|---|
| 区块脏标记 | `setUnsaved(true)` |
| 高度图 | 增量更新，或整段重算 |
| 光照 | 经引擎光照引擎排队（`ThreadedLevelLightEngine`）；不再自写光照算法，这也彻底绕开了旧版 `with_cython.pyx` 那套问题 |
| 方块实体 | 覆盖时移除旧实体（修复旧版缺陷），新状态需要时创建 |
| POI | 跟随方块变化更新（`ServerLevel#onBlockStateChange` 一类逻辑 **[待验证]**）|
| 客户端同步 | 少量变化用段更新包，大量变化重发整区块；需要按 1 MiB 上限分批 **[上限已核实]** |
| 方块计数 | 重算 `LevelChunkSection` 的非空计数 |

**任务调度：**

- 主线程每 Tick 预算默认 15 ms，世界冻结时可放宽到 40 ms；配置可调。
- 可以在工作线程计算（读取段快照后计算结果），最终写入回到主线程。
- 任务可暂停、可取消。取消时通过 WAL 回滚已应用部分。
- 单用户串行，不同用户在选择不相交时可并行，相交时排队。

**失败隔离：** 每个方块实体/实体的 NBT 读写包 try-catch。NeoForge 自身也只记录这类异常后继续（`EntityStorage` 的补丁就是这样）**[已核实]**。失败项进入任务报告，而不是中断整个任务。

### 5.5 历史系统（替代 `revisionhistory.py`）

**目标：** 修复星形缺陷，支持崩溃恢复，支持跨会话，适配“即时生效”语义。

**磁盘布局：**

```text
<世界>/mcedit_reborn/history/<维度键>/
  manifest.json            # formatVersion、DataVersion、MC 版本、加载器、模组列表、配额
  wal/<revId>.wal          # 执行中才存在
  revs/<revId>/
    meta.json              # 作者、时间、描述、父 ID、受影响范围、统计
    sections.bin           # 变更段的前镜像（调色板编码 + 压缩）
    block_entities.nbt     # 前镜像
    entities.nbt           # 前镜像，以 UUID 索引
    biomes.bin
    chunks/                # 被整体删除的区块：region/entities/poi 三份原始字节
    files/                 # level.dat、玩家数据等文件级修改
```

**算法：**

1. 真正的**线性反向增量链**。撤销时读取该修订的前镜像写回，并把当前状态写成后镜像，用于重做。链式结构由测试锁定，直接以旧版星形缺陷为反例。
2. **先写日志**：前镜像 fsync 后才开始修改世界；完成后原子重命名。启动时发现未完成的 WAL 就提示回滚。
3. **冲突检测**：每段存储“操作后状态的哈希”。撤销时如果世界已被他人或游戏改动，就报告差异，并让用户选择“强制覆盖 / 跳过冲突段 / 取消”。
4. **段级稀疏存储**：调色板感知 + 压缩。优先用 1.21.1 已自带的 LZ4 **[待验证类路径可直接使用]**，否则用 JDK Deflater；后台可再压缩。
5. **配额**：默认每世界 2 GB、最多 500 步，超过时淘汰最旧修订，并在 UI 提示。
6. **文件级修改纳入历史**：`level.dat` 和玩家数据修改也可撤销（旧版有此能力）。
7. **UI 状态的撤销**（选择框等）只在客户端，与世界修订同处一个 UndoStack 视图，与旧版两层设计一致。

**历史面板支持：** 列出修订、跳转到任意修订、查看影响范围、查看前后对比高亮。

### 5.6 选择系统

- 类型：Box、Shape（Square/Round/Diamond/Cylinder/ParabolicDome + Chunk）、Union、Intersection、Difference、Inverted、Hollow。
- 可序列化描述，由服务器重新构造并校验，不接受客户端发来的体素数据。
- 插件形状通过注册表 ID + 参数引用。
- Y 范围支持负数，统一从 `WorldPort` 获取。
- 新增（低成本高价值）：按方块类型筛选选择、选择扩张/收缩、保存命名选择。
- 修复第 1.4 节列出的代数缺陷，并用属性测试验证：`contains` 与 `sectionMask` 必须一致；德摩根律成立；`sections()` 覆盖所有非空段。

### 5.7 变换（移动、克隆、旋转、镜像、缩放）

| 变换 | 方块状态 | 方块实体 | 实体 |
|---|---|---|---|
| Y 轴 90° 倍数 | `BlockState#rotate`，模组可自行覆盖 | 修正位置；内容不主动改动 | 修正位置和朝向 |
| 镜像 | `BlockState#mirror` | 同上 | 同上 |
| X/Z 轴 90° 倍数 | 原版没有 API，用基于属性的通用规则（继承 `rotation.py` 的思路：`facing`、`axis`、`half`、`shape` 等）+ 可由数据包扩展的覆盖表 | 同上 | 位置 |
| 任意角度/缩放 | 几何最近邻，状态不旋转，并在 UI 明确告知 | 跳过或按最近位置复制（选项） | 变换位置 |

- 旧版没有镜像表，也丢弃实体和方块实体，这些都是新版要补上的。
- 悬挂实体（画、物品展示框）和带乘客的实体要专门处理；每个复制出的实体重新分配 UUID。
- `BlockEntityTransformer` 注册点：模组或插件可以为自己的方块实体提供内部字段变换（例如朝向、绑定坐标）。
- **绝对坐标引用无法通用修复**（例如模组里的绑定位置）。任务报告要提示，不做静默修改。

### 5.8 文件格式

| 格式 | 读 | 写 | 说明 |
|---|---|---|---|
| Sponge Schematic v3（`.schem`） | ✅ | ✅（默认） | 主要交换格式；旧版没有 |
| 原版结构（`.nbt`） | ✅ | ✅ | 旧版只能导出且不写 `DataVersion`（`structure.py`）；含多调色板 |
| Litematica（`.litematic`） | ✅ | Phase 8 后 | 社区常用 |
| 旧 MCEdit/WorldEdit（`.schematic`） | ✅ | ❌ | 用 `BlockStateData` 把数字 ID 转成状态 **[类已核实存在]**；实体和方块实体走 DFU；`BlockIDs` 映射表用于识别旧模组方块 |
| Sponge v1/v2 | ✅ | ❌ | 经 DFU 升级 |
| 内部剪贴板（`.mcerb`） | ✅ | ✅ | 带版本、`DataVersion`、模组清单、缺失方块保留 |

**跨版本与跨模组策略：**

- 所有导入都比较 `DataVersion`。更旧的经 `DataFixPort` 升级；**更新的默认拒绝**，并可以选择“尽力而为”。
- 模组数据不走 DFU，所以如实保留。
- 导入前的预检报告：缺失方块列表（可映射、可替换成空气，或保留占位）、缺失实体、版本差异。
- 上传由分片协议完成（32 KiB 上限），服务器设总大小和频率上限。

### 5.9 视图与渲染

**总体策略：** 相机视图直接用原版 `LevelRenderer` 渲染世界，从而天然得到模组渲染效果和 Sodium/Iris 兼容；其他视图由自有渲染器实现。

**相机与玩家（设计决定）：**

采用“**玩家即相机**”：编辑器模式下，真实玩家切成无碰撞的隐身飞行状态，由客户端控制器移动。这样原版的区块追踪自动跟随相机，不必另造流式传输。

- 退出时恢复（第 5.1 节的持久化附件）。
- 高速移动可能触发原版的“移动过快”校验 **[待验证]**，专用服务器上改由专用 Payload 驱动移动。
- 备选方案是客户端独立相机实体 + 自有区块流，更灵活但复杂得多，预研失败时再考虑。

**视图清单：**

| 视图 | 实现 | 控制（沿用旧版） |
|---|---|---|
| **相机** | 原版世界渲染 + 覆盖层 | WASD/Space/C；右键转向，短按切换粘滞鼠标视角；中键平移；可切正交；视距；工作平面 |
| **2D 切面** | 自有渲染：按切片构建网格，离屏渲染到纹理，显示在 ImGui 面板 | 右键平移；滚轮切层；X/Y/Z 轴；标尺；**新增缩放**（旧版没有） |
| **俯视** | 瓦片光栅化（高度图 + 地图颜色/平均纹理色） | 右键平移；滚轮以光标为中心 ×2 缩放；标尺 |
| **小地图** | 同俯视的瓦片系统，显示视锥轮廓 | 新增点击跳转 |
| **Iso / 四分格** | 旧版代码已失效，属于新增。四分格 = 相机 + 3 个切面面板 | Phase 6 尾或 1.1 |

**覆盖层 `RenderPort`：**

- 提交阶段使用 `RenderLevelStageEvent`（1.21.1 含 `AFTER_SKY`、`AFTER_SOLID_BLOCKS`、`AFTER_TRANSLUCENT_BLOCKS`、`AFTER_LEVEL` 等阶段 **[已核实]**）。
- 图元：线、面、盒、线框、半透明块；`engine/editor` 只产出顶点和样式。
- 视觉语义继承旧版：选择框蓝色半透明面 + 3px 线框 + 被遮挡时半透明线；光标面高亮；Shift 移动模式绿色。

**幽灵预览（导入/克隆/笔刷）：**

- 用虚拟 `BlockAndTintGetter` 包住待放置内容，通过 `BlockRenderDispatcher` 构建网格，用半透明渲染；方块实体用 `BlockEntityRenderDispatcher` 渲染。
- 这样模组方块预览和最终结果一致。
- 克隆重复的预览按实例复用网格。

**自有渲染器的网格构建：**

- 工作线程构建，有主线程提交预算。
- 优先级沿用旧版的螺旋顺序。
- 脏段增量重建，**同时考虑相邻段**（旧版只重建变化的区块，不会使邻居失效，这是显示 bug 的来源）。

**图层开关（继承并更新）：**

| 图层 | 状态 |
|---|---|
| 物品实体 / 经验球 / 画 | 保留 |
| 怪物位置（X 光线框） | 保留，改为所有实体类型可筛选 |
| 方块实体位置 | 保留 |
| 命令方块颜色/位置 | 保留，按命令名上色 |
| 物品展示框 | 保留 |
| 待执行 Tick（`block_ticks`/`fluid_ticks`） | 保留 |
| 区块/段边界 | 保留 |
| 高度图（新增类型选择） | 保留 |
| 刷怪点 | 重做：用引擎的刷怪判定，而不是旧版已废弃的规则 |
| 区块状态 | 把旧版不可达的 TerrainPopulated 叠加层改为显示 `Status` |
| 命令可视化（目标、连线） | 重做：用 Brigadier 解析真实语法，不再迁移 `commandblock.py` |
| 光照值 | 新增（低成本） |

**射线拾取：**

- 客户端级别，改用方块实际形状（`BlockState#getShape`），替代旧版的全立方体假设。
- 保留“穿过实心块直到遇到空气”的语义，作为“相机在方块内”时的选项，并修复旧版在缺失段边界上的穿透问题。
- 工作平面优先规则、最大距离 200 的回退沿用。
- 2D 视图不走射线，直接坐标映射。

**Sodium/Iris：**

- Sodium 替换区块渲染，需验证 `RenderLevelStageEvent` 是否按预期触发 **[待验证]**。
- Iris 光影包启用时，覆盖层和自定义着色器可能出错。兜底策略：检测到光影时提示用户，提供“编辑器模式临时关闭光影”的选项。

### 5.10 UI

**技术选择：Dear ImGui（imgui-java）**

| 优点 | 代价 |
|---|---|
| 与 Minecraft GUI API 完全解耦（R5），版本迁移零成本 | 需随模组分发原生库 |
| 自带停靠、表格、树、拖拽、多视口纹理，适合编辑器 | 观感不像原版 |
| 开发速度快，插件也容易写面板 | **中文输入法（IME）候选框需专门验证** |
| 业内已有模组采用（Flashback 等，**[记忆，未在本次核实]**） | JNI 类名固定，无法重定位，与其他自带 ImGui 的模组可能冲突 |

**这是首要预研项。** Phase 0 必须在 NeoForge 1.21.1 上验证：

1. Jar-in-Jar 打包 + 原生库提取加载（Windows x64、Linux x64、macOS arm64/x64）。
2. 与 Minecraft 的 GLFW 回调共存（链式），鼠标捕获切换正常。
3. **中文显示**（动态字形范围或预置字库）和**中文输入**（IME 候选位置、预编辑）。
4. Minecraft 使用的 LWJGL 与 imgui-java 后端的版本兼容性。
5. 帧开销达标；不被 GL 状态污染（渲染前后保存/恢复状态）。

**未通过时的回退：** 在 `mcedit-ui` 内实现精简的自研保留模式控件库，走 `UiRenderBackend` 绘制，时间预算增加约 4–6 人周。原版 `Screen` 控件**不作为**回退，因为这恰好是后续版本变化最剧烈的 API。

**Vulkan 冗余：** Mojang 已开始把渲染后端转向 Vulkan，26.x 快照提供了选项，公开资料中关于 26.4 是否默认启用的说法不一致。所以 ImGui 不直接绑定 GL 后端：`UiRenderBackend` 接口接收 `ImDrawData`。1.21.1 用 GL 实现，将来再写 Blaze3D 实现。纹理句柄同样抽象。

**布局（对应旧版结构）：**

```text
┌ 菜单栏：世界 / 编辑 / 选择 / 导入导出 / 区块 / 插件 / 窗口 / 选项 / 帮助 ──────┐
├ 工具栏：选择 移动 克隆 笔刷 泛洪 生成 │ 检查方块/实体/区块 │ 维度 │ 保存 历史 库 ┤
├──────────┬────────────────────────────────────────────┬───────────┤
│ 小地图    │                                            │ 检查器     │
│ 工具选项  │             相机视图（全窗渲染）             │ 面板       │
│          │        （或切面/俯视/四分格面板）             │           │
├──────────┴────────────────────────────────────────────┴───────────┤
│ 状态栏：坐标 区块 生物群系 方块名[状态] FPS TPS 任务进度                     │
└──────────────────────────────────────────────────────────────────────────┘
```

- 所有面板可停靠、浮动，布局持久化。
- 中文优先：UI 字符串全部走翻译键，随模组提供 `en_us`、`zh_cn`，复用 Minecraft 语言文件机制。方块名用游戏翻译，因此模组语言自动生效。

### 5.11 网络、多人与安全

**协议：** `mcedit_reborn:*`，走 `RegisterPayloadHandlersEvent` / `PayloadRegistrar`，协议版本号独立管理。服务器未安装时，客户端以“无编辑能力”状态运行，不应被踢出，声明可选依赖。

**强制安全规则：**

| 规则 | 说明 |
|---|---|
| 服务器权威 | 所有写操作经服务器校验，客户端只提交意图 |
| 权限 | 默认 OP 等级 2+，可配置；接入 NeoForge `PermissionAPI`，细分到操作类型 |
| 解码限长 | 所有集合用 `ByteBufCodecs.collection(ctor, codec, maxSize)`，绝不自己实现 `readCollection`。这是官方 2026-05 漏洞公告的直接要求 **[已核实]** |
| 服务端校验 | 体积上限、操作范围上限、频率限制、并发任务上限 |
| NBT 上传 | 严格深度和大小限制；命令方块编辑额外要求权限 |
| 文件路径 | 原理图库只能访问限定目录，防止路径穿越 |
| 审计 | 专用服务器记录谁在何时改了哪里（`logs/mcedit_reborn-audit.log`） |
| 无隐藏外联 | 不内置遥测和崩溃上报；Mojang 皮肤走游戏自身机制 |

**多人编辑（1.0 范围）：** 历史按用户隔离；撤销前做冲突检测；可显示其他编辑者的选择框；区域软锁（可选）。

### 5.12 插件 API

**机制：** 插件就是普通 NeoForge 模组，依赖 `mcedit-api`，通过 NeoForge 自定义注册表注册内容：

| 注册表 | 内容 | 旧版对应 |
|---|---|---|
| `mcedit_reborn:tool` | 工具 | `registerToolClass`（旧版其实不生效） |
| `mcedit_reborn:command` | 菜单命令 | `CommandPlugin` |
| `mcedit_reborn:simple_command` | 带选项的命令 | `SimpleCommandPlugin` |
| `mcedit_reborn:generator` | 生成器（含 L-system 基类） | `GeneratePlugin`、`LSystemPlugin` |
| `mcedit_reborn:brush_shape` | 笔刷形状 | 旧版硬编码，**新增** |
| `mcedit_reborn:brush_mode` | 笔刷模式 | 旧版硬编码，**新增** |
| `mcedit_reborn:inspector` | 方块实体/实体检查器 | `registerBlockInspectorWidget` |
| `mcedit_reborn:be_transformer` | 方块实体内部变换 | 新增 |
| `mcedit_reborn:format` | 导入导出格式 | 新增 |

**示例（目标 API，最终以实现为准）：**

```java
public final class FillWithChance extends SimpleCommand {
    static final Options OPTIONS = Options.builder()
        .blockState("block", "minecraft:stone")
        .floatSlider("chance", 0.5f, 0f, 1f)
        .build();

    @Override public Options options() { return OPTIONS; }

    @Override public void perform(EditContext ctx, Selection sel, OptionValues opt) {
        var block = opt.blockState("block");
        float chance = opt.getFloat("chance");
        ctx.edit(sel, (x, y, z, current) ->
            ctx.random().nextFloat() < chance ? block : current);
    }
}
```

**原则：**

- API 不暴露任何 `net.minecraft` 类型，插件就能跨版本。这是版本冗余对生态的直接收益。
- 需要访问游戏内部的插件可以用 `platform` 逃生舱，但不受兼容承诺保护。
- 所有编辑经 `EditContext`，从而自动进入历史和权限体系，不像旧版把整个会话暴露给插件。
- 语义化版本，`@ApiStatus` 标注稳定性。
- 选项值持久化（旧文档声称支持，实际没有实现）。
- 脚本语言（KubeJS 联动或 GraalJS）放到 1.0 之后评估。
- 随附示例：把 `city`、`hilbert`、`koch`、`simple_options`、`count_blocks`、`world_editing_demo` 用新 API 重写。`storagedrawers` 改造成“如何给模组方块写检查器”的教程。

### 5.13 配置、日志与错误处理

- 配置：NeoForge `ModConfigSpec`（服务端预算、权限、配额）+ 客户端偏好 JSON（布局、键位、最近使用）。
- 键位：全部可重绑（旧版 viewcontrols 的能力），支持导入导出。
- 错误：编辑器内错误面板，可一键复制报告（含版本、模组清单）；不外发。
- 日志：独立日志文件，含任务级跟踪；UI 内日志查看器（旧版做了但没接入）。

---

## 6. 功能对照矩阵

状态：**保留**=语义不变重写；**改造**=按 1.21.1 调整；**补全**=旧版是空壳，这次实现；**新增**；**放弃**。阶段见第 9 节。

### 6.1 世界与会话

| 旧功能 | 处置 | 新方案 | 阶段 |
|---|---|---|---|
| 世界列表/最近世界 | 改造 | 融入原版世界选择界面，增加入口和最近记录 | P3 |
| Minecraft 安装/版本/资源包管理 | 放弃 | 游戏内无需此功能 | - |
| 自动下载 1.12 jar | 放弃 | - | - |
| New World | 放弃 | 用原版创建 | - |
| Open / Close / Save | 改造 | 见第 5.1 节 | P3 |
| Save As | 补全 | 世界另存（复制到新目录） | P8 |
| Backup | 补全 | 备份世界 | P8 |
| Repair | 补全 | 区域文件检查修复（只读扫描 + 确认修复），独立工具 | P8 |
| 只读查看模式 | 保留 | 观察模式，无编辑权限时使用 | P3 |
| session.lock 窃取 | 放弃 | 游戏自身持锁 | - |
| 维度切换 | 改造 | 列出所有注册维度，含模组维度 | P3 |
| 时间、亮度滑块 | 改造 | 仅本地预览，不改世界时间 | P6 |
| 单实例检测、IPython 控制台、ObjGraph、Heapy、Pastebin | 放弃 | 调试工具用 Spark 等现成方案 | - |
| 语言切换 | 改造 | 跟随游戏语言 | P3 |

### 6.2 编辑命令

| 旧功能 | 处置 | 新方案 | 阶段 |
|---|---|---|---|
| Undo/Redo/历史 | 改造 | 第 5.5 节 | P2/P3 |
| Cut/Copy/Paste | 保留 | 剪贴板在服务器端（大内容）+ 本地文件 | P4 |
| Paste Blocks / Paste Entities | 补全 | 粘贴选项 | P4 |
| Delete / Delete Blocks / Delete Entities | 保留，修复菜单丢失 | | P4 |
| Fill | 保留 | 新增方块图案（按权重混合） | P2/P4 |
| Replace Blocks | 保留 | 支持标签、属性通配（`*[waterlogged=true]`） | P4 |
| Find Blocks | 补全 | 结果列表，点击跳转 | P5 |
| Find/Replace Items | 补全 | 搜索容器、实体、玩家中的物品，支持组件匹配 | P5 |
| Find/Replace 命令文本 | 补全 | 含正则、选择器 | P5 |
| Find/Replace NBT | 保留，修复 | 名称/值/类型/路径全部可用 | P5 |
| Analyze | 保留 | 按状态计数，TXT/CSV 导出，可按模组分组 | P5 |
| Select All / Deselect | 保留 | | P3 |
| Export/Import Schematic | 改造 | 第 5.8 节 | P4 |
| Export 结构 `.nbt` | 保留，补导入 | | P4 |
| 库面板 | 改造 | 原理图库（支持全部格式、缩略图预览）；地图库；自定义方块页放弃 | P5 |

### 6.3 工具

| 旧工具 | 处置 | 新方案要点 | 阶段 |
|---|---|---|---|
| Select | 保留 | 普通/经典/粘滞三模式，形状，坐标与尺寸输入，修复“手动输入不可撤销” | P3 |
| Move | 保留 | 补上实体随移（旧版只清方块，与文档不符）、镜像 | P4 |
| Clone | 保留 | 重复 1–10000、旋转重复、旋转偏移；缩放计入偏移（旧 TODO） | P4 |
| Brush | 保留 | 5 种形状、Fill/Replace/Biome 模式、空心、悬停距离、拖拽插值、整笔画合并一次撤销；新增平滑、噪声模式（旧版留有 `brushNoise` 未完成） | P4 |
| Flood Fill | 改造 | 忽略状态选项，液体判定改用标签/流体状态（旧版写死 ID 8–11） | P4 |
| Generate | 保留 | 实时预览，L-system 基类，城市等示例 | P7 |
| Inspect Block | 保留 | 显示属性、光照、方块实体编辑 | P5 |
| Inspect Entity | 保留 | 射线选取、列表、编辑 | P5 |
| Inspect Chunk | 改造 | 见 6.5 | P5 |
| 工具快捷键 | 新增 | 旧版已注释掉 | P3 |

### 6.4 面板与编辑器

| 旧功能 | 处置 | 新方案 | 阶段 |
|---|---|---|---|
| 玩家面板 | 改造 | 在线/离线分流，背包、属性、NBT 树，相机↔玩家同步；补全副手和旁观模式 | P5 |
| 世界信息 | 改造 | 名称、难度、出生点、时间、游戏规则（旧版缺）；`WorldGenSettings` 只读显示种子和生成器 | P5 |
| 地图面板 | 改造 | `data/map_*.dat`、`idcounts.dat`、`map_id` 组件；图片导入并量化（使用游戏的地图色表，不再用 `adapter.py:1168` 的旧色表）；放置到展示框 | P5 |
| 背包编辑器 | 改造 | `id/count/components`；物品图标来自游戏；组件编辑器 + SNBT；数据校验 | P5 |
| NBT 树编辑器 | 保留 | 补 Long Array，数组可编辑（旧版只读），SNBT 粘贴，搜索 | P5 |
| 方块选择器 | 改造 | 搜索名称/ID/模组/标签，属性下拉，收藏，最近使用，真实图标；**放弃数字 ID 查询** | P3 |
| Configure Blocks | 放弃 | 注册表自动提供；替换为“缺失方块报告” | - |
| 箱子/发射器/漏斗检查器 | 改造 | 通用容器检查器，按方块实体数据自动适配，模组可注册 | P5 |
| 命令方块检查器 | 补全 | 旧版改动不会写回；含语法校验（Brigadier） | P5 |
| 告示牌检查器 | 改造 | `front_text`/`back_text`，JSON 文本 | P5 |
| 插件管理器 | 改造 | 插件即模组，只提供启用/禁用和信息展示 | P7 |
| 错误对话框 | 改造 | 无外联 | P3 |
| 日志查看器 | 补全 | | P8 |
| 偏好设置 | 改造 | 相机、键位、预算、历史配额 | P3/P8 |
| 待处理导入 | 改造 | 多个待放置导入共存（旧版宣称但未实现） | P4 |

### 6.5 区块操作

| 旧功能 | 处置 | 新方案 | 阶段 |
|---|---|---|---|
| Prune World | 改造 | 三类区域文件同步处理，强确认，可撤销 | P5 |
| Delete Chunks | 改造 | 同上；删除后可选让游戏重新生成 | P5 |
| Create Chunks | 补全 | 经引擎生成已选区块，显示进度 | P5 |
| Mark/Unmark Repopulation | 改造 | `TerrainPopulated` 已不存在，改为“标记重新生成”（Status 操作，语义需预研）| P5 |
| 区块检查器 | 改造 | `Status`、`InhabitedTime`、`LastUpdate`、`isLightOn`、`yPos`、结构引用、NBT 树 | P5 |
| 重算光照 | 新增 | 经引擎重新计算 | P5 |
| 重算高度图 | 新增 | | P5 |
| 生物群系工具 | 改造 | 笔刷 Biome 模式 + 选区填充，精度 4×4×4（经 `BiomePort`，见 R8） | P4 |

### 6.6 旧插件与示例

全部示例按第 5.12 节用新 API 重写（P7）。

---

## 7. 1.21.1 数据格式备忘

实现与测试的依据（来源：Minecraft Wiki，**[已核实]**，个别处标注）。

**DataVersion：** 1.21 = 3953，**1.21.1 = 3955**。

**世界目录（1.21.1，26.1 之前的布局）：**

```text
level.dat, level.dat_old, session.lock
region/  entities/  poi/  data/  playerdata/  advancements/  stats/  datapacks/
DIM-1/{region,entities,poi,data}   DIM1/{...}
dimensions/<命名空间>/<路径>/{region,entities,poi,data}
```

> 该布局中“下界/末地仍在 `DIM-1`、`DIM1`”这一点，是由 Wiki 的 26.1 更新记录反推的，Phase 0 用实际 1.21.1 世界确认。

**区块 NBT 根键：**
`DataVersion`、`xPos`、`zPos`、`yPos`、`Status`、`LastUpdate`、`sections`、`block_entities`、`Heightmaps`、`fluid_ticks`、`block_ticks`、`InhabitedTime`、`blending_data`、`PostProcessing`、`structures`、`isLightOn`（`isLightOn` 来自经验，Wiki 页面未列，待确认）。

**NeoForge 附加键 [已核实]：** `neoforge:attachments`（区块附件）、`LevelChunkAuxiliaryLightManager.LIGHT_NBT_KEY`（方块辅助光照）。**`level.dat` 根上有 `fml.LoadingModList`**，列出 `ModId`、`ModVersion`，只记录模组列表，**不再有数字 ID 映射**，可用于检测缺失模组。

**段：**

- `Y`、`block_states{palette, data}`、`biomes{palette, data}`、`BlockLight`、`SkyLight`。
- 方块索引 = `y*256 + z*16 + x`，4096 个。
- 位宽 = `max(4, ceil(log2(调色板大小)))`，**索引不跨 long**；单一调色板时省略 `data`。
- 生物群系 64 个单元，位宽 = `ceil(log2(调色板大小))`，无最小值，不跨 long。
- 光照数组 2048 字节，偶数索引在低半字节。

**高度图：** `MOTION_BLOCKING`、`MOTION_BLOCKING_NO_LEAVES`、`OCEAN_FLOOR`、`OCEAN_FLOOR_WG`、`WORLD_SURFACE`、`WORLD_SURFACE_WG`；主世界 9 位每项，每 long 7 项，共 37 long；值从世界底部算起。

**区域文件：**

- 头部 8 KiB：位置表（3 字节偏移 + 1 字节长度）、时间戳表；单区块最多 255 扇区。
- 载荷：4 字节长度 + 1 字节压缩类型 + 数据；**最后一块必须补齐到 4096 字节**。
- 压缩类型：1 gzip、2 zlib、3 无、4 LZ4、127 自定义（带命名空间字符串）。
- 超过 1020 KiB：类型值 +128，长度置 1，数据存到 `c.<x>.<z>.mcc`。

**实体文件：** `DataVersion`、`Position`（int 数组）、`Entities`（列表）。

**POI 文件：** `Sections.<Y>.{Valid, Records[{pos, free_tickets, type}]}`、`DataVersion`。

**结构文件：** 根 `DataVersion`、`size`、`palette` 或 `palettes`、`blocks[{state, pos, nbt}]`、`entities[{pos, blockPos, nbt}]`；数据包路径 1.21 起是 `data/<ns>/structure/`。

**物品：** `{id, count, components}`。实体 UUID 是 4 元 int 数组。属性键 1.21 起是 `attributes`、`id`、`base`、`modifiers`（`operation` 为字符串）。

**网络载荷上限 [已核实]：** 客户端到服务器 < 32 KiB，服务器到客户端 ≤ 1 MiB。

---

## 8. 版本兼容路线

### 8.1 按真实变更检验架构

下表基于 NeoForge 官方迁移文档的梳理（部分长文只读了前段，详见附录 B），用来验证第 4 节的隔离是否站得住。结论：**已查阅到的变更都落在适配层或 `FormatProfile`，业务层预计不用动。** 这是设计层面的推演，要等第一个新版本适配完成才算真正验证。

| 版本 | DataVersion | 影响编辑器的破坏性变更 | 吸收位置 |
|---|---|---|---|
| 1.21.2/3 | 4080/4082 | `blit` 签名、着色器 `ShaderInstance`→`CompiledShaderProgram`；实体渲染状态化；容器 `Lock` 变 `lock` | RenderPort、EntityPort；格式靠游戏自身处理 |
| 1.21.4 | 4189 | 物品模型 `items/` 新体系，物品渲染走 `ItemStackRenderState` | ResourcePort（物品图标） |
| 1.21.5 | 4325 | **NBT API 重写**（`getXOr`、`contains` 移除）；票据 API 改名；**`RenderPipeline`、`GpuBuffer`**；部分方块实体默认值省略 | NbtBridge、WorldPort、RenderPort |
| 1.21.6–8 | 4435–4440 | **`ValueInput/ValueOutput`**；GUI 两阶段重写；字符串颜色需要 alpha | BlockEntityPort、EntityPort；ImGui 不受 GUI 影响 |
| 1.21.9/10 | 4554/4556 | 方块实体渲染器 `submit` 模型；输入事件改对象；`KeyMapping.Category`；`FMLEnvironment.getDist()` | RenderPort、InputPort、平台入口 |
| 1.21.11 | 4671 | **`ResourceLocation`→`Identifier`**；`RenderTypes`/`RenderSetup`；采样器分离 | 适配层；核心用 `Id` 不受影响 |
| 26.1 | 4786 | **Java 25、取消混淆**；`GuiGraphicsExtractor`；`ChunkPos` 工厂方法；**世界目录重排到 `dimensions/minecraft/*`** | 新适配层 + FormatProfile |
| 26.2 | 4903 | Vulkan 选项（实验性） | UiRenderBackend、RenderPort |
| 26.3 | 5023 | **调色板键 `Name/Properties`→`id/properties`**；状态合并 `terrain` | FormatProfile |
| 26.4（快照） | 5119+ | **生物群系精确到每方块**；`Status`→`status` | BiomePort、FormatProfile |

> 这张表也说明一个事实：如果把业务逻辑写在 Minecraft API 上，1.21.5、1.21.6、1.21.11、26.1 每一次都要大面积修改。

### 8.2 新增版本的标准流程

1. 复制最接近的 `platform-*` 模块，按端口逐个修复。
2. 新增 `FormatProfile`（按 DataVersion 区间）。
3. 跑 TCK；未通过的项要么修复，要么显式声明“能力降级”（`PlatformCapabilities`）。
4. 跑黄金世界回归和模组冒烟。
5. 单独发布对应版本的 jar；`core/engine/ui/editor/api` 不改代码。

**预估：** 相邻小版本 1–3 人周，跨大变动（如 26.1）3–6 人周。这是根据 8.1 变更规模做的估算，第一个适配完成后应该回填实际数字。

### 8.3 建议的版本优先级（供决策，不属于 1.0）

1. **1.21.1 NeoForge**：当前唯一实现目标。
2. **1.21.11 NeoForge**：1.21 线的末端版本，能覆盖 1.21.5/6 两次大变动。
3. **26.x NeoForge**：Java 25 + 新目录；建议等 Vulkan 路线稳定。
4. **Fabric**：按需，模块结构已留位。

**版本策略规则：** 同时维护的版本不超过 3 个；不追每个小版本；旧版本转入只修严重 bug。

### 8.4 现在就要做的冗余（1.21.1 阶段的额外成本）

| 工作 | 成本 | 不做的后果 |
|---|---|---|
| 多模块拆分 + 依赖约束 | 约 3 天 | 后期难以拆开 |
| 自有 `Id`、`NbtNode`、`InputEvent` | 约 1 周 | 每次 API 改名都波及全库 |
| TCK 测试框架 | 约 1 周（随开发增量） | 适配层无法验证 |
| `FormatProfile` 只实现 3955 一项，但结构就绪 | 约 2 天 | 目录和键名硬编码 |
| `UiRenderBackend` 抽象 | 约 2 天 | Vulkan 时重写 UI 接入 |
| `PlatformCapabilities` 能力声明 | 约 1 天 | UI 里散落版本判断 |

合计约 3–4 人周，已计入总工期。**不做的冗余**（避免过度设计）：不提前实现其他版本的适配；不抽象 Minecraft 的全部功能；`mc1211-common` 与 `-neoforge` 的拆分只求简单可用，直到真的需要 Fabric 时才细化。

---

## 9. 路线图

工作量按一名熟悉 NeoForge 的全职开发者估算。

### P0 预研与工程基线（2–3 周）

**目标：把所有高风险假设变成已知事实。** 每项都是一个小型可运行 demo，结果写入 `docs/spikes/`。

| 预研项 | 通过标准 | 不通过时 |
|---|---|---|
| S1 ImGui 集成（含中文显示与 IME、三平台原生库） | 见 5.10 | 转自研 UI，加 4–6 周 |
| S2 玩家即相机（高速飞行、区块跟随、退出恢复） | 单人与专用服务器都不被踢出 | 转独立相机 + 自有区块流 |
| S3 温区块加载（不产生新地形，不触发副作用） | 见 5.3 | 世界边缘走冷通道 |
| S4 快速路径写段（光照、高度图、客户端同步正确） | 与 `/fill` 结果比对一致；百万方块达标 | 调整预算或默认走安全路径 |
| S5 覆盖层 + 幽灵预览（含模组方块）与 Sodium/Iris 共存 | 三种环境显示正确 | 增加兼容分支或提示 |
| S6 自有切面渲染到纹理并显示在 ImGui 中 | 模组方块正确，帧耗可接受 | 降级方案 |
| S7 确认实际 1.21.1 世界结构（第 7 节未核实项） | 文档更新 | - |

**同时：** `git init`，旧代码归档到 `legacy/`，建多模块工程、CI、依赖约束和 ArchUnit 规则。

**验收：** 输出预研报告；复核总工期；确认 D1–D5。

### P1 核心与格式（3–4 周）

- `Id`、`NbtNode`（读写、SNBT）、几何、`StatePalette`。
- 选择代数（属性测试）、掩码运算。
- 区域文件读写（全部压缩类型、`.mcc`）、调色板编解码、`FormatProfile(3955)`。
- 原理图：Sponge v3、结构 `.nbt` 读写；旧 `.schematic` 读取（ID 表先做接口）。

**验收：** 不启动游戏即可通过全部单测；读写 `testworlds` 后语义不变；旧版 `test_files/*.schematic` 可解析。

### P2 编辑引擎与历史（4–5 周）

- 平台端口 1.21.1 实现（World、Block、BlockEntity、Entity、Biome）。
- 任务调度、预算、快速/安全路径、收尾步骤。
- 历史：WAL、反向链、冲突检测、配额、崩溃恢复。
- **无 UI 交付：** 用命令 `/mce fill|replace|copy|paste|undo|redo` 驱动。

**验收：** GameTest 全绿；强杀恢复测试通过；随机操作序列撤销后世界一致；第 2.1 节指标首次达标。

### P3 编辑器外壳（4–5 周）

- 编辑器模式进出（含崩溃恢复）、相机控制、键位。
- ImGui 主布局、菜单、工具栏、状态栏、任务进度、错误面板。
- 选择工具（BoxHandle 完整语义）、覆盖层、射线拾取。
- 方块选择器、维度切换、历史面板、世界选择界面入口。

**验收：** 可以进入世界、框选、填充、撤销、退出，全程不碰命令。**首个可演示版本（Alpha 1）。**

### P4 核心工具（5–6 周）

- 剪贴板、导入导出 UI、预检报告、多个待放置导入。
- 变换引擎（旋转、镜像、缩放、实体和方块实体）。
- Move、Clone、Brush（全部形状与模式）、Flood Fill、生物群系工具。
- 幽灵预览。Replace 支持标签和属性通配。

**验收：** MCEdit 老用户能完成“复制建筑 → 旋转 → 克隆阵列 → 刷地形”的完整工作流。**Alpha 2。**

### P5 检查器与数据编辑（4–5 周）

- NBT 树、物品/背包编辑器、方块/实体/区块检查器。
- 玩家、世界信息、地图面板。
- 查找替换（方块、物品、命令、NBT），分析。
- 区块操作（Prune、删除、创建、重算）。原理图库。

**验收：** 第 6 节矩阵 P5 项全部完成。**Beta 1。**

### P6 多视图（3–4 周）

- 切面、俯视、小地图（瓦片缓存），标尺。
- 图层系统，命令可视化，刷怪点。四分格。

**验收：** 视图之间选择、定位同步；大世界小地图不阻塞。

### P7 插件 API（2–3 周）

- 注册表、`mcedit-api` 冻结候选、生成工具、L-system、示例重写、开发文档。

**验收：** 独立样例模组仅依赖 `mcedit-api` 就能添加一个工具、一个笔刷、一个生成器和一个检查器；API 无 `net.minecraft` 类型（由测试保证）。

### P8 服务器、加固与兼容（3–4 周）

- 专用服务器全流程，权限，审计，配额，攻击性测试（超长集合、畸形 NBT、路径穿越）。
- 兼容矩阵测试，性能优化，世界工具（Repair、Backup、Save As），日志查看器。
- Litematica 写入。

**验收：** 第 2 节所有指标达标；公开 Beta 无严重数据问题。

### P9 发布（2 周）

- 文档（中/英）、教程、从 MCEdit/WorldEdit 迁移指南。
- 第三方依赖许可清单，发布到 Modrinth/CurseForge，1.0。

**总计：约 32–41 人周。**

### 依赖关系与并行机会

```text
P0 → P1 → P2 → P3 → P4 ─┬→ P5 ─┐
                        ├→ P6 ─┼→ P8 → P9
                        └→ P7 ─┘
```

P5、P6、P7 互相独立，有第二名开发者时可并行，日历时间能压缩约 6–8 周。

---

## 10. 测试策略

### 10.1 分层

| 层 | 工具 | 覆盖 | 运行频率 |
|---|---|---|---|
| 单元/属性 | JUnit 5 + jqwik，无需启动游戏 | core、engine、编解码、选择代数、历史算法 | 每次提交 |
| 架构 | ArchUnit | 依赖铁律、API 无 MC 类型 | 每次提交 |
| 集成 | NeoForge GameTest（MDG `gameTestServer` 运行）| 操作管线、撤销、方块实体、实体、光照、同步 | 每次提交（CI） |
| 契约 | TCK | 每个平台适配 | 每次提交，新版本必跑 |
| 黄金世界 | 自建固定世界 + 期望摘要 | 回归 | 每日 |
| 手工/录屏 | 脚本化清单 | UI 交互手感 | 每里程碑 |
| 兼容 | 模组包矩阵 | 第 10.4 节 | 每个 Beta |

### 10.2 必备的破坏性测试

- **随机操作序列**：生成 N 个随机操作，全部撤销再全部重做，比较世界摘要。
- **崩溃注入**：在 WAL 写入前/中/后、应用中、提交前强杀，下次启动验证一致性。
- **星形缺陷回归**：保存后连续多步撤销（旧版缺陷的直接反例）。
- **未知数据往返**：注入未知键、未知方块、未知实体，操作后必须原样保留。
- **格式合规**：把工具写出的区域文件交给干净的专用服务器加载，日志中不得有格式错误。
- **模糊测试**：畸形原理图、畸形 Payload。
- **`.mcc` 与超大区块**：覆盖旧版 `regionfile.py:255` 的问题场景。

### 10.3 TCK 示例

```java
@Tck void setSection_thenRead_returnsSameStates(PlatformFixture f) { ... }
@Tck void blockEntityNbt_roundTrip_preservesUnknownKeys(PlatformFixture f) { ... }
@Tck void deleteChunk_removesRegionEntitiesAndPoi(PlatformFixture f) { ... }
@Tck void rotate90_fourTimes_isIdentity(PlatformFixture f) { ... }
@Tck void worldHeightIsQueried_notAssumed(PlatformFixture f) { ... }   // 用自定义高度维度
```

### 10.4 兼容矩阵（建议，以 1.21.1 实际可用版本为准）

| 类别 | 对象 | 重点 |
|---|---|---|
| 渲染 | Sodium、Iris（含常见光影包）| 覆盖层、预览 |
| 大型内容模组 | Create、Mekanism、Applied Energistics 2、Ars Nouveau | 多方块结构、方块实体、动态渲染、复制粘贴一致性 |
| 世界生成 | 带自定义维度和高度的模组 | 高度查询、维度路径 |
| 建筑相关 | WorldEdit、Chisel 类模组 | 共存、方块状态数量 |
| 性能 | C2ME 类、Lithium 类 | 区块 IO 假设 |
| 大型整合包 | 一个 200+ 模组的主流整合包 | 启动时间、方块选择器性能 |

---

## 11. 风险登记

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| ImGui 中文 IME 体验差 | 中 | 高（中文用户直接受影响） | S1 首要验证；回退为自研输入控件或系统 IME 浮窗 |
| 与其他自带 ImGui 的模组冲突 | 中 | 中 | 版本探测、清晰报错；必要时自编译改名的原生绑定 |
| 温区块加载产生新地形或副作用 | 中 | 高 | S3；世界边缘走冷通道；不确定时提示 |
| 快速路径破坏模组多方块状态 | 中 | 高 | 默认保守；标签白名单；任务报告；可撤销 |
| 模组方块实体含绝对坐标，复制后失效 | 高 | 中 | 明确告知；`BlockEntityTransformer`；与模组协作 |
| Sodium/Iris 覆盖层问题 | 中 | 中 | S5；提示和降级 |
| 玩家即相机在服务器上被反作弊拦截 | 中 | 中 | 专用 Payload；服务器配置；备选方案 |
| 大区域操作导致内存压力 | 中 | 高 | 流式处理、滑动窗口、预估后提示 |
| 历史占用过多磁盘 | 中 | 中 | 配额、压缩、淘汰提示 |
| 依赖的原版内部 API 被其他模组 Mixin 改写 | 中 | 中 | 优先用公开路径，TCK 进入矩阵测试 |
| 单人开发周期过长导致范围失控 | 高 | 高 | 严格按阶段；P3、P4 后即可发 Alpha；P6、P7 可推迟 |
| 后续版本改动超出预期（Vulkan、26.x） | 中 | 中 | 第 4 节隔离；不提前追；能力声明 |
| 未来 Mojang/NeoForge 政策或 EULA 问题 | 低 | 中 | 不分发游戏资产，只读取运行时内容 |

---

## 12. 许可与合规

| 项 | 处理 |
|---|---|
| 项目许可 | BSD-3-Clause；保留 David Rio Vierra 原版权声明和免责条款（即使是重写，只要借鉴了实现细节和资产就应保留）；修正旧的 MIT/BSD 矛盾 |
| 名称 | BSD 第三条限制未经许可用原项目名为衍生品背书。建议独立名称，并注明“灵感与设计来自 MCEdit 2” |
| `demjson`（LGPLv3+） | 不迁移 |
| nbticons（MIT 式，Justin Aquadro） | 可沿用并保留许可 |
| `assets_raw/playerskins/` | **不使用**，无授权信息 |
| 其他图标 | 来源不明，建议**重绘或选用明确许可的图标集** |
| 方块数据 JSON、`BlockDumper.java` | 不再需要；不分发 Mojang 衍生转储 |
| 测试世界 | 自行生成；`Station.schematic` 等第三方文件只留在 `legacy/`，新测试不依赖 |
| Sentry DSN、Pastebin key | **不迁移**；如果你将来公开这份旧代码，建议先清除，并通知上游 |
| imgui-java、Dear ImGui | MIT，发布时附清单 |
| 用户隐私 | 无遥测，无隐藏外联 |

---

## 13. 旧代码处置

1. 先 `git init`，把当前状态作为第一个提交，打标签 `legacy-mcedit2`。
2. 移动到 `legacy/mcedit2/`，只读保留，供实现时查阅。
3. 最终发布包不含 `legacy/`。
4. 迁移的不是代码，而是一份**行为规格**：第 1.3 节的设计，整理成 `docs/behavior/*.md`，配合测试。

---

## 14. 启动清单（批准后的第一周）

1. 确认 D1–D5。
2. `git init`，归档旧代码。
3. 用官方 MDK 1.21.1 分支生成基线，拆成第 4.4 节的多模块。
4. 配置 CI（GitHub Actions：构建 + 单测 + GameTest）。
5. 启动 S1（ImGui）和 S2（相机）预研。这两项决定 UI 和交互架构，优先级最高。
6. 用 1.21.1 原版和 NeoForge 分别生成测试世界，核对第 7 节未确认项。

---

## 附录 A：旧模块 → 新模块映射

| 旧 | 新 | 备注 |
|---|---|---|
| `mceditlib/nbt.pyx`、`nbtattr.py` | `core.nbt` | 属性描述符改为类型化视图 |
| `mceditlib/pc/regionfile.py` | `engine.region` | 补全压缩类型，修复写入缺陷 |
| `mceditlib/anvil/adapter.py`、`worldfolder.py` | `engine.format` + `platform.WorldPort`/`ColdChunkPort` | |
| `mceditlib/worldeditor.py` | `platform.WorldPort` + `engine.session` | |
| `mceditlib/revisionhistory.py` | `engine.history` | 重新设计 |
| `mceditlib/selection/` | `core.selection` | |
| `mceditlib/transform.py`、`rotation.py` | `engine.transform` + `BlockPort` | |
| `mceditlib/block_copy.py`、`operations/` | `engine.ops` | |
| `mceditlib/relight/` | **删除** | 交给引擎 |
| `mceditlib/multi_block.py`、`fakechunklevel.py` | **删除** | NumPy 特化、被段掩码替代 |
| `mceditlib/schematic.py`、`structure.py`、`export.py` | `engine.formats.*` | |
| `mceditlib/blocktypes/*` | **删除**，由 `BlockPort` 提供 | |
| `mceditlib/anvil/entities.py` | `EntityPort`、`BlockEntityPort` + 类型化视图 | |
| `mceditlib/minecraft_server.py`、`pocket.py`、`java.py` | **删除** | 已失效 |
| `mcedit2/editorsession.py` | `editor.EditorSession` | 拆小 |
| `mcedit2/editorapp.py`、`main.py` | `platform.neoforge` + `ui.MainWindow` | |
| `mcedit2/editortools/` | `editor.tools` + `ui.tools` | |
| `mcedit2/editorcommands/` | `editor.commands` + `ui` | |
| `mcedit2/panels/`、`widgets/`、`dialogs/`、`ui/*.ui` | `ui.*` | ImGui 重写 |
| `mcedit2/rendering/` | `RenderPort` + 适配层 + `editor.overlay` | 模型加载全部删除 |
| `mcedit2/worldview/` | `editor.view` + `CameraPort` | |
| `mcedit2/handles/boxhandle.py` | `editor.handles` | 语义原样继承 |
| `mcedit2/plugins/` | `mcedit-api` | 重新设计 |
| `mcedit2/synth/` | `editor.generate.lsystem` | 算法可移植 |
| `mcedit2/util/commandblock.py` | 删除，改用 Brigadier | |
| `mcedit2/util/minecraftinstall.py`、`resourceloader.py`、`textureatlas.py` | **删除** | 游戏内不需要 |
| `mcedit2/util/player_server.py` | 删除，使用游戏皮肤系统 | |
| `mcedit2/util/bresenham.py` | `core.geometry` | 只留一个实现 |
| `util/demjson.py`、`sentry.py`、Pastebin、IPython、Heapy、ObjGraph | **删除** | |

## 附录 B：资料来源与未覆盖项

**本次已查阅：**

- 仓库的 Python/Cython 源码。例外：`demjson.py` 未读，`flowlayout.py`（Qt 示例）只看了文件头，`benchmarks/` 和 `.ui` 文件为略读。旧代码未运行。
- NeoForge 1.21.1 分支源码与补丁：`CommonHooks`、`AttachmentHolder`、`ChunkSerializer`、`EntityStorage`、`StructureTemplate`、`RenderLevelStageEvent`。
- NeoForge Maven（21.1.265、MDG 2.0.148）、imgui-java 发布信息。
- [NeoForge 1.21.1 文档](https://docs.neoforged.net/docs/1.21.1/gettingstarted/)：入门、模组文件、Payload、附件、注册表、事件。
- [NeoForge 迁移文档](https://docs.neoforged.net/primer/docs/1.21.2)（1.21.2–26.1）、[ModDevGradle](https://github.com/neoforged/ModDevGradle)。
- [NeoForge 网络漏洞公告](https://neoforged.net/news/mitigating-vulnerabilities-network/)（2026-05）。
- [Chunk format](https://minecraft.wiki/w/Chunk_format)、[Region file format](https://minecraft.wiki/w/Region_file_format)、[Java Edition level format](https://minecraft.wiki/w/Java_Edition_level_format)、[Data version](https://minecraft.wiki/w/Data_version)、[Structure file](https://minecraft.wiki/w/Structure_file)、[Entity format](https://minecraft.wiki/w/Entity_format)。
- [mappings.dev](https://mappings.dev/1.21.1/net/minecraft/world/level/chunk/storage/ChunkStorage.html)（确认部分 1.21.1 类和方法存在）。
- Vulkan 方向：[Gigazine 报道](https://gigazine.net/gsc_news/en/20260224-minecraft-opengl-vulkan)、[playnews 关于 26.4 的说明](https://www.playnews.gg/en/guides/minecraft-snapshot-26-4-vulkan-rendering-how-to-enable-it-and-check-it-with-f3)，均为二手来源，且 26.4 是否默认启用说法不一。

**已知未覆盖（使用前需复核）：**

- 迁移文档部分长页只读了前段：1.21.2、1.21.5、1.21.9、1.21.11、26.1；26.2、26.3 迁移文档未读。第 8.1 表在这些版本上可能不完整。
- 第 5 节提到的部分具体方法名（如 `onBlockStateChange`、世界备份 API、LZ4 类路径可用性）来自经验，已标 [待验证]。
- Axiom、Flashback、Amulet 的最新情况未能查实，第 3 节相应措辞保守。
- 所有性能指标和工期都是规划目标，未经实测。
