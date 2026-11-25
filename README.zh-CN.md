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

- 桌面版从左栏「偏好设置」右侧展开的列表进入「MCP 设置」，勾选「启用 MCP 服务」。按客户端所用方式复制配置并保存。已有配置时，可以将 `appstore-uploader` 加到现有的 `mcpServers` 中。
- `stdio` 由客户端启动。使用超级小爱等表单式客户端时，选择 `stdio` 并填写配置里的 `command`、`args` 和 `env`。修改默认输出目录后重新连接即可生效。
- Streamable HTTP 和服务器推送事件（SSE）各有开关、端口和连接令牌，默认都关闭。启用对应服务并保存后，桌面应用运行期间可连接。默认地址分别为 `http://127.0.0.1:18765/mcp` 和 `http://127.0.0.1:18766/sse`。在超级小爱里选择 HTTP 或 SSE，填写配置里的 `url` 和 `Authorization` 请求头。
- 开启服务即可解析包、查询商店记录、预览更新和处理素材。勾选「允许投递、草稿操作和安装截图」可使用会提交、删除草稿或安装软件的工具。商店查询和更新预览可复用桌面登录态，也可通过客户端环境变量 `APPSTORE_USERNAME` 和 `APPSTORE_PASSWORD` 配置账号。
- 源码版可用 `scripts/appstore-mcp` 独立启动，优先使用项目的 `.venv`。使用 `--transport http` 或 `--transport sse` 启动本机端点，`--port` 可指定端口。可通过 `APPSTORE_MCP_HTTP_TOKEN` 或 `APPSTORE_MCP_SSE_TOKEN` 设置对应的 Bearer 令牌。参考[stdio 示例配置](appstore/examples/uos-ai-mcp.example.json)。

#### 工具用法

- `inspect_packages` 支持 `.deb`、`.uab` 和 `.layer`，区分 `loongarch64` 和 `loong64`。`sync_capabilities` 刷新商店能力缓存，`get_capabilities` 按包类型和架构查询系统线和可选版本。
- `list_my_apps` 返回应用和草稿的商店 ID（`app_id`）和版本记录 ID（`detail_id`）。`get_app_workflow` 按商店 ID 查询历次更新，按版本记录 ID 查询审核步骤。`get_app_history_notes` 按版本记录 ID 读取原始更新说明。
- 更新指定版本时，用 `prepare_app_update` 传入 `detail_id` 预览换包、资料和系统线，再将同一组修改传给 `submit_app_update`。将 `operation` 设为 `submit_update` 提交更新、`save_draft` 保存原草稿或 `submit_draft` 送审原草稿。`delete_draft` 删除选中的草稿。
- `preprocess_assets` 按桌面规则处理图标和截图。`start_capture_packages` 默认在本机安装应用，在虚拟显示器里截图。用返回的 `job_id` 调用 `get_job_status` 查看状态、截图路径和报告。`capture_packages` 是同步入口。
- 首提用 `prepare_new_app_workbook` 根据真实安装包生成投递表，补齐缺项后用 `validate_workbook` 校验。`upload_workbook` 按表提交新应用或批量更新，设置 `dry_run` 可仅校验。`generate_example_template` 用于 LabelNova 演示。
- `upload_packages` 是按包名或 `app_id` 匹配已有应用的旧版换包入口。`start_auto_upload_packages` 在后台选一个包截图，达到 3 至 6 张截图要求后，把包和截图一起提审。用返回的 `job_id` 查询结果。`auto_upload_packages` 是同步入口。截图后提审使用 API 模式。
- 批量工具正式投递时需要账号密码，可在客户端 `env` 中设置 `APPSTORE_USERNAME` 和 `APPSTORE_PASSWORD`。
- 默认的 `sudo` 安装和卸载命令使用 `APPSTORE_SUDO_PASSWORD`，省略时走 `pkexec`。`click-text:<界面文字>` 步骤通过 OCR 点击当前窗口中的匹配文字。可用 `scripts/setup-ocr-venv` 安装 OCR，再把 `APPSTORE_OCR_PYTHON` 指到 `./.venv-ocr/bin/python3`。
