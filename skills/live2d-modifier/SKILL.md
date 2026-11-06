---
name: live2d-modifier
description: 从合法取得、已绑定的 Live2D Cubism 模型出发，依据风格描述或参考图将其改为新配色、空间渐变与表面纹样，同时保留原有动作绑定。适用于 Live2D 模型换装改色、添加渐变与花纹、处理共享贴图冲突、多姿态撕裂排查及安全导入 VTube Studio。
---

# Live2D 模型外观改款（live2d-modifier）

本 Skill 指导 Agent 使用 `l2d-mod` CLI（Python 包 `live2d_modifier`）对已经完成绑定的 Live2D Cubism 模型进行非破坏式外观改款。它不是单纯的色相滑块，也不是从零拆层重绑，而是在保留全部既有变形器、参数与物理绑定的前提下，通过高分辨率渲染采集、外部重绘、部件级色彩拟合与 UV 残差回投，将模型改为新的配色、空间渐变与表面纹样。

> **状态说明**：核心工具链已在私有原型实验中验证，公开版仓库测试目前处于待验收状态。不同模型的拆层与 UV 结构差异较大，执行时需按下方检查方法逐项核验，不能跳过多姿态动作检查。

## 适用场景与边界

- **适用场景**：
  - 根据文字风格要求或参考图，为已有 `.moc3` 模型更换整体配色、发色/服装空间渐变、表面印花或妆容。
  - 调整模型原生开关参数、隐藏冗余部件、借用既有配饰槽位（保持原绑定）或拆分存在共享贴图冲突的网格。
  - 将改款后的模型以新名称与新 `ModelID` 安装进 VTube Studio。
- **不适用场景**：
  - 从零把单张立绘拆层并绑定成新模型。
  - 新增网格拓扑、Umamo 加面、`.cmo3` 源文件往返或任意骨架手术。

## 环境前提

运行 `l2d-mod` 需要 Python 3.11+（`uv`）、Node.js 与 Playwright Chromium，并由用户按 Live2D 官方许可自行提供 `live2dcubismcore.min.js`。本项目不包含任何模型资产或专有 SDK。

执行任何命令前，先确认 CLI 可用：

```bash
uv run l2d-mod --help
```

## CLI 命令速查

示例统一使用 `/path/to/model`、`work/`、`PartExample`、`ArtMeshExample` 等占位符。`render` 与 `transfer` 的详细参数请运行 `uv run l2d-mod render --help` 和 `uv run l2d-mod transfer --help` 查看，不要猜测未定义的参数。

- `inventory`：盘点模型的部件（Part）、网格（ArtMesh）、参数范围与贴图页。
- `render`：按指定姿态、表情或参数渲染单帧图像。
- `sheet`：批量渲染多姿态与表情联系表，用于基准存档与改款后动作核验。
- `capture`：导出高分辨率全身/头部渲染图，并记录屏幕像素到贴图 UV 的对应关系。
- `pick`：查询渲染图指定像素位置对应的 `PartExample` 与 `ArtMeshExample`。
- `conflicts`：检测共用同一块贴图 UV 区域的网格冲突。
- `variant`：在新目录生成模型变体。支持修改参数默认值、`hide`/`unmask`/`blend`、`affine`/`tint`、`texture`/`split` 等原位操作；日常改款默认只调用完成外观修改所需的操作。
- `page`：新增或克隆贴图页，配合贴图拆分实现冲突解耦。
- `transfer`：对齐重绘图像，执行部件级明暗颜色映射拟合与 UV 残差回投。
- `texels`：对贴图局部区域做确定性的像素级修补或微调。
- `moc check`：检查 `.moc3` 与 `.model3.json` 的结构完整性与贴图引用。
- `vts`：以新模型名与新 `ModelID` 安装到 VTube Studio。
- `serve`：启动本地浏览器预览服务。

---

## 完整七步工作流

### 第 1 步：读取输入并只读盘点模型

确认原模型目录（如 `/path/to/model`）、风格描述或参考图路径，以及独立工作目录（如 `work/`）。**原模型目录全程保持只读，禁止直接覆盖原模型文件。**

```bash
uv run l2d-mod inventory /path/to/model -o work/inventory.json
uv run l2d-mod sheet /path/to/model -o work/orig_sheet.png
uv run l2d-mod conflicts /path/to/model -o work/conflicts.json
```

若需要确认画面某处属于哪个部件或网格，先用 `render` 出图，再用 `pick` 查询屏幕坐标对应的 `PartExample` 或 `ArtMeshExample`。

### 第 2 步：调整原生开关与拆解共享贴图冲突

根据 `inventory.json` 与 `conflicts.json` 的结果，判断是否需要在采集前生成整理后的基准变体模型 `work/base_model`：

1. **原生参数开关与显隐**：如果模型自带外套切换、配饰开关等参数，可通过 `variant` 修改参数默认值，或用 `hide` 隐藏当前风格不需要的部件；若借用已有配饰槽位，保持其原有变形器绑定不变。
2. **共享纹理冲突拆页**：检查 `conflicts.json`。例如当画师署名网格 `ArtMeshExample` 需要保留原貌，却与待重绘的服装共用同一处贴图时，使用 `variant`（`texture`/`split`）或 `page` 将冲突网格拆分到独立贴图页，避免后续重绘服装时污染署名。

```bash
uv run l2d-mod variant /path/to/model -c work/variant_config.json -o work/base_model
uv run l2d-mod moc check work/base_model
```

### 第 3 步：高分辨率采集与外部图像编辑

对基准模型执行高分辨率全身与头部近景 `capture`，同时记录屏幕到贴图 UV 的对应关系：

```bash
uv run l2d-mod capture work/base_model -o work/captures
```

将 `work/captures` 中导出的全身与头部渲染 PNG 交给外部图像编辑器重绘（可使用 Agent 已安装的图像生成/编辑 Skill，或交由用户人工编辑；本项目不绑定特定计费 API）。

**图像编辑硬性约束**：
- 外部编辑器只读取 `capture` 导出的渲染图，不直接改原始贴图页。
- 严格保持原图的身体姿势、外轮廓边界与内部几何线稿位置不变，只修改配色、空间渐变、表面纹样与妆容。
- 若使用全身 + 头部多视角重绘，保持两张图的色彩与光影基调一致。

### 第 4 步：对齐图像与色彩/残差回投（`transfer`）

先运行 `uv run l2d-mod transfer --help` 查看完整参数，再将编辑好的渲染图传给 `transfer` 生成新贴图：

```bash
uv run l2d-mod transfer work/base_model --captures work/captures -o work/styled_model
```

`transfer` 内部执行两层处理：
- **按部件拟合明暗颜色映射**：为每个部件计算整体色彩映射并铺满整个 UV 岛，确保静态姿态下受遮挡的部位（如内层头发、眼睑、衣摆内侧）在运动露出时拥有一致底色。
- **UV 残差回投**：把重绘图上的空间渐变、服饰印花与面部妆容通过 UV 反投影写入贴图；同时传入全身与头部近景时，近景高分辨率采样优先加权。
- **特殊网格 `overrides`**：对半透明网格、正片叠底（Multiply）阴影层或左右镜像共用同一块 UV 的部件，在配置中使用 `overrides` 显式约束（如仅做基础色映射、锁定 alpha 或调整残差权重），防止叠色发脏或镜像穿帮。

### 第 5 步：局部像素确定性微调（`texels`）

回投完成后，若贴图局部边缘、领口交界或特定小部件需要精确修补，使用 `texels` 对目标贴图页执行确定性的像素微调，避免为小瑕疵反复重跑外部生图。

```bash
uv run l2d-mod texels work/styled_model --help
```

### 第 6 步：多姿态与运动验证（必做，禁止只看静态正视图）

静态正视图完好不代表模型可交付。跨独立运动网格的细小装饰或越界残差在模型做动作时会裂开，必须完成以下验证：

1. **二进制与结构检查**：
   ```bash
   uv run l2d-mod moc check work/styled_model
   ```
2. **八组标准动作与近景检查**：使用 `sheet` 和 `render` 渲染并逐图检查以下姿态的全身与头部近景：
   - `front`（正面基准）
   - `blink`（闭眼/眨眼：检查眼睑与睫毛交界是否露旧底色或粘连）
   - `talk`（张嘴说话：检查唇周、口腔内侧与下巴边缘）
   - `smile`（微笑表情：检查脸颊红晕与眼周变形）
   - `turn_l`、`turn_r`（左右转头：检查发丝分层、侧脸轮廓与耳部遮挡处）
   - `look_up`（抬头：检查下颌线与颈部阴影遮挡区）
   - `sway`（身体摆动/倾斜：检查裙摆、袖口、胸前配饰与多层头发摆动）
3. **修复跨网格裂纹（`residual_holes`）**：
   - 若在 `blink`、`talk`、`turn_l`、`turn_r`、`sway` 等动作下发现跨独立运动网格的小图案发生撕裂或错位，用 `pick` 定位对应网格或贴图区域，在 `transfer` 配置中将其加入 `residual_holes`。
   - 重跑 `transfer` 以排除该处高频残差并恢复阶段一的平滑基础着色，随后重新渲染动作表确认裂纹消失。
4. **交互预览（可选）**：运行 `uv run l2d-mod serve work/styled_model` 在浏览器中进一步检查连续参数过渡。

### 第 7 步：输出完整模型与安装（`vts`）

确认全部检查通过后，最终模型完整保存在新输出目录（如 `work/styled_model`），原模型目录 `/path/to/model` 保持完好未改。

- 若用户要求装入 VTube Studio，运行 `vts` 并指定新的模型名称。`vts` 会自动分配全新 `ModelID` 并写入独立目录，绝不覆盖原模型：
  ```bash
  uv run l2d-mod vts work/styled_model --name "StyledModelVariant"
  ```
- 若用户要求打包交付，可将用户自有的输出模型目录打包交给用户；但注意本项目代码仓库与公开软件包内永不存放任何模型素材、贴图、截图或角色配方。
