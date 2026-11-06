# 配方与命令契约

示例 ID 均为占位符，需要从自己的 `inventory`、`capture` 和 `pick` 结果选择。配方与编辑图保存在用户工作目录，可换一套风格重跑；它们不是软件代码的一部分。

## 工作副本、拆页与保留

`l2d-mod variant SOURCE DEST recipe.json`。`DEST` 必须是新目录。相对的配饰图片路径按当前工作目录解释。

```json
{
  "params": [{"id": "ParamExample", "default": 1}],
  "hide": {"meshes": ["MeshUnused"]},
  "split": [{"groups": [{"meshes": ["MeshSignature"]}]}]
}
```

把不该随衣服重画的署名网格拆到独立页，并在 transfer 配方 `keep` 中保留它。拆页不删除署名。`texture` 可给已有配饰槽指派自己的透明底图；先用 `page test` 确认朝向，`page matte` 可抠纯色底。`affine`、`blend`、`unmask` 只在已查清槽位时使用，不重新绑定。

## 渲染与选择

`inventory SOURCE -o work/inventory` 输出目录中有 `parts.txt`、`params.tsv`、`inventory.json`、`expressions.json`。`capture SOURCE work/body` 输出前缀，不是目录；`work/body_in.png` 才是给图像编辑器看的图。

`pick work/body --box 100,100,200,200 --show work/selected.png` 查看选中的部件。`conflicts work/body --include-hidden` 读取同一次 capture 的数据。修改结构后重新 capture，旧对应关系不能继续用。查看头部时按实际画面调整 `--zoom 倍率,中心X,中心Y`，中心坐标相对全身模型，不假定不同模型脸都在同一位置。

## 回填与共享纹理

```json
{
  "affine_trim": 50,
  "keep": ["MeshSignature"],
  "borrow": {"MeshTranslucent": "MeshOpaqueReference"},
  "no_residual": ["MeshFlatAccessory"],
  "fill_hidden": ["MeshHiddenBack"]
}
```

`transfer SOURCE DEST --cap work/body=work/body_edit.png work/head=work/head_edit.png --overrides work/transfer.json`。后列的近景优先；仍保留低权重处原有细节。`borrow` 借颜色映射，不是拷贝网格；适用于透明层没有干净样本、镜像部件没有自有拟合。`alpha` 是按 mesh ID 给不透明度缩放的字典。`no_residual` 只做基础着色，`keep` 完全不改。默认 `affine_trim: 50`，避免未对准样本把纹样回填位置拉偏。

空间渐变、印花来自残差回投，不能关闭所有 residual 后宣称渐变效果已经复现。没在任何视角出现的像素只能取得推断的底色，不会凭空获得隐藏纹样；必要时补拍视角。

## 去除裂开的纹样

在编辑图里圈出导致穿帮的花饰，保留同一个基准模型、capture 和编辑图，重跑 transfer 到新目录：

```json
{
  "residual_holes": {
    "ref": "head",
    "polys": [[[100,100],[160,100],[160,170],[100,170]]],
    "dilate": 2,
    "feather": 3
  }
}
```

`ref` 是 capture 前缀的文件名末段，例如 `work/head` 的 `head`。坐标是该 capture 的像素坐标，不是 UV 或部件 ID。洞内仅移除新增残差，恢复原线稿经过基础着色的结果。左右共用纹理时，两边都要检查。

## 确定性微调

`texels SOURCE DEST work/texels.json`：

```json
{"ops":[{"op":"recolor","meshes":["MeshRibbon"],"hue":[110,190],"to":{"L":70,"C":30,"h":300},"contrast":1.0}]}
```

支持 `recolor`、`saturate`、`smooth`、`match`。`L/C/h` 是 Lab 的亮度、色度与色相，不是 RGB。明暗相对中位数保留；白色/中性区不会因普通色相选择自动着色。

## 运动与交付

`motion SOURCE -o work/frames --frames 48 --fps 12` 生成连续转头、眨眼、说话帧，逐帧查看；可自行用 ffmpeg 合成。通用参数名不一定驱动所有模型，先在 params.tsv 查实并确认转头确实发生，不能把静止模型算作通过。结构修改后 `moc check` 仅是第一关，实际运动与视觉质量仍要审阅。

`package SOURCE -o work/delivery.zip` 解引用贴图链接，只打包模型引用资源、表情与 VTS 配置，排除运行记录。`vts SOURCE --name 新名称` 默认拒绝覆盖已存在目录。上述命令交付用户自己的模型，和 GitHub 软件 release 是两个不同动作。
