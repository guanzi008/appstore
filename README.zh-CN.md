# 统信应用投递助手

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

UOS AI MCP：

- MCP 启动入口：`scripts/appstore-mcp`
- 示例配置：[appstore/examples/uos-ai-mcp.example.json](appstore/examples/uos-ai-mcp.example.json)
- 关键工具：
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
