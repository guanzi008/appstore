# appstore

这是从 `/home/hao/Documents/ai/WWW/appstore` 独立出来的项目。

快速开始：

```bash
cd /home/hao/Documents/ai/appstore
python3 -m pip install -r requirements.txt
python3 -m appstore.upload_batch --help
```

文档入口：

- 中文说明：[appstore/README.zh-CN.md](appstore/README.zh-CN.md)
- 英文说明：[appstore/README.md](appstore/README.md)

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
