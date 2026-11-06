# 技术设计文档（RFC）：live2d-modifier-skill

- **状态**：草案（基于已验证私有原型整理，公开版待验收）
- **CLI 名称**：`l2d-mod`
- **Python 包名**：`live2d_modifier`
- **运行环境**：Python 3.11+（`uv`）、Node.js 18+、Playwright Chromium
- **开源许可**：MIT（不含 Live2D Cubism Core 及任何模型资产）

## 1. 架构概览

`live2d_modifier` 采用 Python 主控 + Headless Chromium WebGL 渲染桥接的双层架构：

1. **无头渲染与几何采样层（Node.js + Playwright Chromium）**
   - 在本地启动受控页面，加载用户自行提供的 `live2dcubismcore.min.js` 与模型目录。
   - 支持写入任意参数组合或标准姿态预设，输出确定性的 RGBA 帧（`render`、`sheet`）。
   - 在 `capture` 与 `pick` 模式下，读取 Cubism Core 运行时变形后的每个 `ArtMesh` 顶点坐标、UV 坐标、不透明度、绘制顺序与混合模式，建立屏幕像素与贴图纹理坐标的精确映射。
2. **色彩拟合与 UV 回投层（Python / NumPy / Pillow）**
   - 接收 `capture` 导出的映射数据与外部重绘后的目标 PNG，执行两阶段重着色（部件级颜色映射 + UV 残差反投影）。
   - 支持多视角加权融合、`overrides` 部件级策略覆盖、`residual_holes` 裂纹区残差剔除以及 `texels` 确定性像素修改。
3. **模型原位修改与交付层（Python）**
   - 在新目录复制并修改 `.moc3` 与 `.model3.json`：支持参数默认值改写、部件显隐与混合模式调整、仿射与染色、贴图页扩充与冲突网格拆页（`variant`、`page`、`conflicts`）。
   - 提供二进制结构与引用一致性校验（`moc check`）、本地交互预览（`serve`）以及 VTube Studio 非覆盖式安装（`vts`）。

## 2. 仓库目录结构

```text
live2d-modifier-skill/
├── pyproject.toml              # 定义 live2d_modifier 包与 l2d-mod 脚本入口
├── package.json                # 前端渲染桥依赖声明
├── README.md                   # 面向用户的效果说明、用法与安装指南
├── LICENSE                     # MIT 许可证
├── skills/
│   └── live2d-modifier/
│       └── SKILL.md            # 面向 Claude Code / Codex / Cursor / OpenCode 的根 Skill
├── src/
│   └── live2d_modifier/
│       ├── cli.py              # l2d-mod 命令行路由
│       ├── inventory.py        # 部件、网格、参数与贴图盘点
│       ├── renderer.py         # Playwright Chromium 渲染、sheet、capture、pick、serve
│       ├── conflicts.py        # 共享 UV 区域冲突检测
│       ├── variant.py          # 原位模型变体操作（默认参数、hide/unmask/blend、affine/tint、texture/split）
│       ├── page.py             # 贴图页新增与索引重映射
│       ├── transfer.py         # 部件颜色映射拟合与 UV 残差回投
│       ├── texels.py           # 贴图局部像素确定性微调
│       ├── moc_check.py        # moc3 / model3.json 结构与资源核验
│       └── vts.py              # VTube Studio 独立名称与新 ModelID 安装
└── tests/                      # 纯 synthetic 数学与贴图测试 fixture（不含任何真实模型）
```

## 3. CLI 接口规范（`l2d-mod`）

命令集沿用私有原型已验证的 13 个入口，不引入未定义的新命令。`render` 与 `transfer` 等命令的细粒度参数通过 `--help` 暴露。

| 子命令 | 职责与核心行为 |
| --- | --- |
| `l2d-mod inventory` | 读取模型目录，输出 `Part`、`ArtMesh`、参数表（最小/默认/最大值）及贴图文件列表。 |
| `l2d-mod render` | 按指定姿态预设或参数键值对渲染单帧 PNG（详见 `l2d-mod render --help`）。 |
| `l2d-mod sheet` | 批量渲染标准动作与表情联系表（含 `front`、`blink`、`talk`、`smile`、`turn_l`、`turn_r`、`look_up`、`sway`）。 |
| `l2d-mod capture` | 导出全身与头部近景高分辨率渲染图，并保存屏幕像素到 `ArtMesh` / UV 坐标的映射数据包。 |
| `l2d-mod pick` | 输入屏幕坐标，返回该处自顶向下的可见 `ArtMeshExample` 与所属 `PartExample`。 |
| `l2d-mod conflicts` | 计算各 `ArtMesh` 在贴图 UV 空间的包围盒与三角网格重叠关系，报告共享同一贴图块的异构网格。 |
| `l2d-mod variant` | 读取 JSON 配置并在新目录生成变体模型，支持修改参数默认值、`hide`/`unmask`/`blend`、`affine`/`tint`、`texture`/`split` 及复用配饰槽位。 |
| `l2d-mod page` | 在模型中克隆或追加贴图页，并更新指定网格的贴图页索引。 |
| `l2d-mod transfer` | 将外部编辑后的图像按部件色彩拟合与 UV 残差回投写入贴图（详见 `l2d-mod transfer --help`）。 |
| `l2d-mod texels` | 按 UV 或像素矩形对指定贴图页执行擦除、填色、羽化或局部拷贝等确定性操作。 |
| `l2d-mod moc check` | 检查 `.moc3` 文件头、偏移表、参数范围与 `.model3.json` 贴图路径及尺寸一致性。 |
| `l2d-mod vts` | 复制模型至 VTube Studio 目录，重写模型名称与全新 `ModelID`，保证原模型不受影响。 |
| `l2d-mod serve` | 启动本地 HTTP 预览服务，方便在浏览器中实时拖动参数并观察运动表现。 |

## 4. 核心算法设计

### 4.1 屏幕到纹理映射（`capture`）

在静止基准姿态下，分别以全身视口与头部高倍率视口渲染模型。对每个可见且正常混合的 `ArtMesh`，将其变形后的二维屏幕顶点与贴图 UV 三角形光栅化到与渲染图同分辨率的索引缓冲中，记录：

- 顶层可见 `ArtMesh` ID 与所属 `Part` ID；
- 插值后的贴图页索引与精确 $(u, v)$ 坐标；
- 像素级可见性与视口分辨率权重（头部近景权重高于全身远景）。

外部图像编辑器仅接收导出的普通 PNG 渲染图。只要重绘图保持原姿势、外轮廓和几何线稿对齐，即可直接使用该映射表回投，无需绑定任何特定生图 API。

### 4.2 两阶段重着色与残差回投（`transfer`）

单纯将重绘图按 UV 贴回贴图，会导致采集姿态下遮挡的部位（如后发内侧、衣摆下方、眼睑遮挡处）在模型运动露出时保留旧颜色，产生明显色块断层。`transfer` 采用两阶段算法解决该问题：

1. **阶段一：部件级明暗颜色映射（Base Color Transfer）**
   - 对每个 `Part` 或 `ArtMesh`，收集其在 `capture` 视图中的所有可见且未受半透明叠加污染的像素对 $(I_{\text{orig}}, I_{\text{edit}})$。
   - 在颜色空间拟合鲁棒的明暗与色彩仿射映射 $T_m$，将该映射作用到该网格在贴图 UV 岛内的全部像素（含不可见的遮挡区域），生成底色一致的基础贴图 $B$。
2. **阶段二：UV 残差反投影（Residual Back-Projection）**
   - 在屏幕空间计算重绘图与基础贴图渲染结果之间的残差 $\Delta = I_{\text{edit}} - \text{Render}(B)$。
   - 利用 `capture` 记录的 $(u, v)$ 映射将 $\Delta$ 反投影回贴图空间，并在有效采样边缘做平滑外推与羽化，叠加到基础贴图 $B$ 上，从而把重绘图中的空间渐变、服饰印花与面部妆容转化为真实贴图像素。
   - 当同时提供全身与头部近景 `capture` 时，重叠区域按视角分辨率权重优先采纳近景细节。

### 4.3 特殊图层控制与撕裂修复（`overrides` 与 `residual_holes`）

- **`overrides`**：针对半透明薄纱、正片叠底（Multiply）阴影层、高光层以及左右对称镜像共用同一块 UV 的网格，在配置中通过 `overrides` 单独指定处理方式（例如仅应用基础色映射、锁定透明度通道、跳过残差回投或指定色彩映射参数）。
- **`residual_holes`**：外部图像编辑器不了解底层网格拓扑，可能在两个独立运动的 `ArtMesh` 交界处画出连续的小装饰或细碎纹样。静态正视图下看不出异常，但在 `blink`、`talk`、`turn_l`、`turn_r`、`sway` 等动作下会沿网格边界裂开。发现此类裂纹后，将对应贴图区域或网格加入 `residual_holes`，算法会在该区域剔除高频 UV 残差并恢复阶段一的平滑基础着色。

### 4.4 共享贴图冲突拆页（`conflicts` + `variant` / `page`）

部分模型为节省贴图面积，会让无需改色的部件（如画师署名网格 `ArtMeshExample`）与需要重绘的服装共用同一贴图区域。修改前先运行 `conflicts` 检出UV重叠对，再通过 `variant`（`texture`/`split`）或 `page` 新增贴图页并将目标网格指向新页，使署名或原貌部件与重绘服装完全解耦。

## 5. 安全与非破坏性设计

- **原件只读**：所有命令默认将源模型视为只读输入，变体与贴图输出至显式指定的 `work/` 或目标目录。
- **VTS 隔离安装**：`vts` 写入 VTube Studio 时强制生成新目录名、显示名称与 UUID 格式的新 `ModelID`。
- **适度工程边界**：直接复用已在原型中验证的确定性操作，不为假想的对抗场景叠加多层状态机或复杂权限包装。
