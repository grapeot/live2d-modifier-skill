# Live2D Modifier

中文工作语言。公开的已有绑定模型外观修改 skill 与 CLI，默认分支 `master`。

- 根入口 `skills/live2d-modifier/SKILL.md`；实现 `src/live2d_modifier/`；验收计划 `docs/test.md`。
- 模型、贴图、渲染、参考图、角色专属配方、凭据与运行记录放仓库外或 ignored `local/`。不要提交这些内容。
- 发布通用代码和算法；不捆绑专有 Cubism Core。运行时由用户提供 Core，第三方渲染器用固定版本、记录来源与许可。
- 原模型只读；所有修改输出到新目录。不要改变原模型、其他项目或已安装模型。
- `uv venv` 后 `source .venv/bin/activate`，用 `uv pip install -e '.[dev]'`；检查 `ruff check src tests`、`pytest -q`、`python -m build`。
- 实质变更记录 `docs/working.md`。commit 仅在授权任务中进行；只 stage 明确文件，提交前审阅 diff 和隐私。master 经 PR、CI 后合并。
- 不引入从零绑定、Umamo 往返或无关服务。功能必须能由独立 agent 按 skill 在新工作目录中复用。
