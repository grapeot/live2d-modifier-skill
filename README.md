# live2d-modifier-skill

`live2d-modifier-skill` 是一个面向 AI 编程助手（Claude Code、Codex、Cursor、OpenCode）与命令行的 Live2D 模型外观改款工具。它从一个合法取得、已经完成绑定的 Live2D Cubism 模型出发，依据文字风格描述或参考图，将模型改为新的配色、空间渐变与表面纹样，同时完整保留原有的动作、表情与物理绑定。

它处理的是两类传统方式之间的空白：单纯调整色相滑块（Hue Slider）无法做出跨部件的空间渐变、局部妆容或服装印花；而从零拆层、重画 PSD 再重新绑骨成本高，且会丢掉原模型精心调校的变形器与物理手感。本项目在既有绑定结构上通过渲染采集、外部重绘、部件级色彩拟合与 UV 残差回投完成改款。

## 效果与工作流

对一个已绑定的 Live2D 模型进行改款时，工具链按以下七个阶段运作：

1. **只读盘点模型结构**：读取原模型目录、风格要求或参考图，盘点部件（Part）、网格（ArtMesh）、参数范围与贴图页。原模型目录全程保持只读，所有中间产物与新模型写入独立工作目录。
2. **原生开关调整与共享贴图解耦**：按目标风格调整模型原生参数默认值，或隐藏不需要的部件；需要复用已有配饰槽位时保留原绑定结构。同时扫描共享贴图冲突，若需要保留原貌的网格（如画师署名或独立配饰）与待重绘服装共用同一块贴图区域，先将它们拆分到独立贴图页。
3. **高分辨率渲染采集与外部图像编辑**：导出全身与头部近景的高分辨率 `capture` 渲染图，并记录屏幕像素到贴图 UV 的映射数据。随后交由用户自选的图像生成工具、Agent 已安装的生图 Skill 或人工画师重绘渲染图。外部编辑器只需处理普通图像，约束条件是保持原图姿势、外轮廓与内部几何线稿不变。项目本身不绑定任何特定计费图像 API。
4. **色彩映射拟合与 UV 残差回投**：`transfer` 将重绘后的图像对齐回模型贴图。算法先按部件拟合明暗与颜色映射，为平时遮挡的部位赋予一致底色；再将重绘图上的空间渐变、服饰纹样与面部妆容作为残差通过 UV 反投影写入实际贴图。多视角输入下近景采样优先加权；半透明层、正片叠底与左右镜像共享贴图的部位通过 `overrides` 单独指定处理方式。
5. **局部像素确定性微调**：对贴图边缘、扣眼或局部纹理细节，使用 `texels` 执行确定性的像素级修正。
6. **多姿态与运动验证**：静态单帧正常不代表动作正常。工作流会渲染 `front`、`blink`、`talk`、`smile`、`turn_l`、`turn_r`、`look_up`、`sway` 等多姿态序列与近景图，重点检查眨眼、说话、转头和摆动时的网格交界。跨越多个独立运动网格的细小装饰在变形时容易撕裂，发现后通过 `residual_holes` 排除该处残差并恢复平滑基础底色。
7. **独立导出与 VTube Studio 安装**：在输出目录生成完整可用的新模型。需要装入 VTube Studio 时，`vts` 命令会分配新的模型名称与 `ModelID`，与原模型并存，不覆盖原文件。

## 在 AI Agent 中使用

将本仓库的 GitHub URL 交给 Claude Code、Codex、Cursor 或 OpenCode，让它安装仓库根目录下的 `skills/live2d-modifier/SKILL.md`。安装后直接向 Agent 描述改款任务即可：

```text
请使用 live2d-modifier skill，读取 /path/to/model 下的模型，参考 work/style_ref.png 的配色与纹样风格，在 work/output_model 生成改款模型，并完成多姿态动作检查。
```

Agent 也可在完成改款与核验后，将用户自有的改款模型目录打包交付给用户。

## CLI 快速上手（`l2d-mod`）

核心功能封装在 Python 包 `live2d_modifier` 中，命令行入口为 `l2d-mod`。以下示例使用 `/path/to/model`、`work/`、`PartExample`、`ArtMeshExample` 等虚构占位符；`render` 与 `transfer` 等命令的完整参数请通过 `--help` 查看。

```bash
# 1. 盘点模型部件、网格、参数与贴图页
uv run l2d-mod inventory /path/to/model -o work/inventory.json

# 2. 检查共享贴图冲突并生成渲染联系表
uv run l2d-mod conflicts /path/to/model -o work/conflicts.json
uv run l2d-mod sheet /path/to/model -o work/baseline_sheet.png

# 3. 基于配置生成工作副本（调整默认参数、隐藏部件或拆分冲突贴图页）
uv run l2d-mod variant /path/to/model -c work/variant.json -o work/base_model

# 4. 采集全身与头部近景渲染图及屏幕-UV 对应数据
uv run l2d-mod capture work/base_model -o work/captures

# 5. 将外部重绘后的图像对齐回投到贴图（详细参数见 l2d-mod transfer --help）
uv run l2d-mod transfer work/base_model --captures work/captures -o work/styled_model

# 6. 校验二进制结构并渲染多姿态动作表（front/blink/talk/smile/turn_l/turn_r/look_up/sway）
uv run l2d-mod moc check work/styled_model
uv run l2d-mod sheet work/styled_model -o work/verify_sheet.png

# 7. 以新名称和新 ModelID 安装到 VTube Studio（可选）
uv run l2d-mod vts work/styled_model --name "StyledModelVariant"
```

### 子命令一览

| 命令 | 用途 |
| --- | --- |
| `inventory` | 解析并导出模型的部件、网格、参数范围与贴图页清单 |
| `render` | 按指定姿态或参数渲染单张图像（详见 `l2d-mod render --help`） |
| `sheet` | 批量渲染标准姿态与表情联系表，用于改款前后对比与动作验收 |
| `capture` | 输出高分辨率全身/头部渲染底图及屏幕像素到贴图 UV 的映射数据 |
| `pick` | 查询渲染图指定坐标对应的 `PartExample` / `ArtMeshExample` 标识 |
| `conflicts` | 检测共用同一块贴图区域的不同网格，定位改色潜在污染点 |
| `variant` | 在新目录生成模型变体，执行参数默认值、显隐、混合模式、染色与贴图拆页等原位修改 |
| `page` | 管理与扩充模型贴图页，配合贴图拆分隔离冲突网格 |
| `transfer` | 拟合部件级明暗色彩映射并将 UV 残差回投到贴图（详见 `l2d-mod transfer --help`） |
| `texels` | 对贴图局部区域进行确定性的像素修补与微调 |
| `moc check` | 校验 `.moc3` 与 `.model3.json` 的结构完整性与资源引用一致性 |
| `vts` | 生成新模型名称与 `ModelID`，将改款模型安全导入 VTube Studio |
| `serve` | 启动本地预览服务，在浏览器中交互检查模型表现 |

## 环境要求与安装

### 依赖环境

- Python 3.11+ 与 [`uv`](https://docs.astral.sh/uv/)
- Node.js 18+
- Playwright Chromium（用于无头 WebGL / Cubism 渲染与像素级 UV 采样）
- Live2D Cubism Core for Web（`live2dcubismcore.min.js`）

### 安装步骤

```bash
# 克隆仓库后安装 Python 环境与 Playwright Chromium
uv sync
uv run playwright install chromium
npm install

# 验证 CLI 入口
uv run l2d-mod --help
```

### 提供 Live2D Cubism Core

本项目遵循原厂许可要求，不在代码库或发行包中打包 Live2D Cubism Core。使用前请从 Live2D 官方渠道下载 Cubism SDK for Web，按其许可协议获取 `live2dcubismcore.min.js`，并通过环境变量或命令参数指定路径（详见 `l2d-mod --help`）。

## 适用边界与开源许可

不同 Live2D 模型的拆层粒度、UV 排布密度、正片叠底蒙版以及变形器绑定方式差异较大。本工具的目标是自动化处理色彩拟合、UV 回投与多姿态检验等繁琐环节，但不承诺对所有模型都能全自动一次成功；遇到高密度共用贴图或跨网格大幅变形纹样时，通常需要结合 `conflicts`、`overrides`、`residual_holes` 与 `texels` 迭代调整。

本项目不包含从零建模、自动拆层绑骨、任意骨架拓扑手术或 `.cmo3` 工程文件往返功能。

仓库内的核心软件自有代码采用 MIT 许可证开源，不包含任何模型文件、贴图、角色配方或专有运行时。改款模型的使用范围仍遵循原模型与所用素材的许可。
