# 应用投递助手

这是用于统信/deepin UOS 应用商店发布与更新的工具集。

快速开始：

```bash
git clone https://github.com/guanzi008/appstore.git
cd appstore
python3 -m pip install -r requirements.txt
python3 -m appstore.upload_batch --help
```

文档入口：

- 中文说明：[README.zh-CN.md](README.zh-CN.md)
- 英文说明：[README.md](README.md)

## GitHub Action

其他项目可以直接把本仓库当作复用 Action，用于已有应用更新。调用方仓库需要先产出 `.deb`、`.uab` 或 `.layer` 包，再传给这个 Action。

只验证账号和能力缓存，不提交应用：

```yaml
      - name: Verify app store credentials
        uses: guanzi008/appstore@main
        with:
          username: ${{ secrets.APPSTORE_USERNAME }}
          password: ${{ secrets.APPSTORE_PASSWORD }}
          test-only: "true"
```

```yaml
name: Update UOS Store App

on:
  workflow_dispatch:
    inputs:
      package:
        description: Package path
        required: true

jobs:
  update-app:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4

      - name: Update app store listing
        uses: guanzi008/appstore@main
        with:
          username: ${{ secrets.APPSTORE_USERNAME }}
          password: ${{ secrets.APPSTORE_PASSWORD }}
          packages: ${{ inputs.package }}
          app-id: "1096227"
          note: "自动更新"
          mode: api
```

多个包用多行传入：

```yaml
          packages: |
            dist/app_1.2.0_amd64.deb
            dist/app_1.2.0_arm64.deb
            dist/app_1.2.0_loong64.deb
```

如果要按 workbook 更新文案、系统线或批量应用：

```yaml
        uses: guanzi008/appstore@main
        with:
          username: ${{ secrets.APPSTORE_USERNAME }}
          password: ${{ secrets.APPSTORE_PASSWORD }}
          workbook: release.xlsx
          mode: api
```

### UOS AI MCP

- 桌面版从左栏「偏好设置」右侧展开的列表进入「MCP 设置」。勾选「启用 MCP 服务」，复制客户端配置并保存，然后在 MCP 客户端导入 JSON。已有配置时，将 `appstore-uploader` 加到现有的 `mcpServers` 中。客户端连接时通过标准输入输出启动服务。修改默认输出目录后重新连接即可生效。
- 使用超级小爱等表单式客户端时，选择 `stdio`，按 JSON 中的 `command`、`args` 和 `env` 填写。安装版支持直接从启动命令连接，已有的 `mcp` 参数也可继续使用。服务支持 `2026-07-28` 的无状态工具调用和 `2024-11-05` 至 `2025-11-25` 的握手协议。
- 开启服务即可解析包、查询商店记录、预览更新和处理素材。勾选「允许投递、草稿操作和安装截图」可使用会提交、删除草稿或安装软件的工具。商店查询和更新预览可复用桌面登录态，也可通过客户端环境变量 `APPSTORE_USERNAME` 和 `APPSTORE_PASSWORD` 配置账号。
- 源码版可用 `scripts/appstore-mcp` 独立启动，优先使用项目的 `.venv`。参考[示例配置](appstore/examples/uos-ai-mcp.example.json)。
- 关键工具：
  - `inspect_packages` 支持 `.deb`、`.uab` 和 `.layer`，区分 `loongarch64` 和 `loong64`。
  - `get_capabilities` 按包类型和架构查询已同步的系统线及可选版本。
  - `list_my_apps` 查询应用及草稿。`get_app_workflow` 查看更新记录和审核状态。`get_app_history_notes` 读取指定记录的原始按包和通用说明。
  - `prepare_app_update` 预览版本资料、安装包和适配范围。`preprocess_assets` 按桌面规则处理图标和截图。
  - `submit_app_update` 支持资料或系统线更新、替换安装包、保存草稿和送审原草稿。`delete_draft` 删除指定草稿。
  - `prepare_new_app_workbook`：按真实包生成新应用提审 workbook
  - `upload_workbook`：按 workbook 执行新应用提审或批量更新
  - `upload_packages` / `auto_upload_packages`：只用于已有应用更新
  - `generate_example_template`：仅生成 LabelNova 演示模板，不能直接用于真实提审
- 权限说明：
  - 可在 MCP JSON 的 `env` 中配置 `APPSTORE_SUDO_PASSWORD`
  - 若未配置，默认 `sudo ...` 安装/卸载命令会自动切到 `pkexec ...`
- OCR 说明：
  - `click-text:<界面文字>` 步骤会先做 OCR，再点击当前窗口里匹配的文字
  - 可用 `scripts/setup-ocr-venv` 把 OCR 运行时安装到 `./.venv-ocr`
  - 在 MCP JSON 的 `env` 中把 `APPSTORE_OCR_PYTHON` 指到 `./.venv-ocr/bin/python3`
