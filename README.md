# 应用投递助手

Standalone UnionTech/deepin UOS app store publishing toolkit.

Quick start:

```bash
git clone https://github.com/guanzi008/appstore.git
cd appstore
python3 -m pip install -r requirements.txt
python3 -m appstore.upload_batch --help
```

Docs:

- Chinese: [README.zh-CN.md](README.zh-CN.md)
- English: [README.md](README.md)

## GitHub Action

Other repositories can use this repository as a reusable GitHub Action to update an existing deepin/UOS app store application. Build your package first, then pass the package path to the action.

Verify credentials without submitting an app:

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
          note: "Automated update"
          mode: api
```

Use a multiline value for multiple packages:

```yaml
          packages: |
            dist/app_1.2.0_amd64.deb
            dist/app_1.2.0_arm64.deb
            dist/app_1.2.0_loong64.deb
```

To update copy, compatibility ranges, or batch applications from a workbook:

```yaml
        uses: guanzi008/appstore@main
        with:
          username: ${{ secrets.APPSTORE_USERNAME }}
          password: ${{ secrets.APPSTORE_PASSWORD }}
          workbook: release.xlsx
          mode: api
```

### UOS AI MCP

- In the desktop app, open Preferences → MCP Settings and enable the service. Copy the configuration for your client's transport, save the settings, and merge the copied JSON into your MCP client configuration.
- The client launches the service over `stdio`. For clients with a setup form, select `stdio` and fill in the copied `command`, `args`, and `env` values. Reconnect after changing the default output directory.
- Streamable HTTP and server-sent events (SSE) have separate switches, ports, and connection tokens. Both default to off. Once enabled, the endpoints are available while the desktop app is running. The default addresses are `http://127.0.0.1:18765/mcp` and `http://127.0.0.1:18766/sse`. Select HTTP or SSE in the client and copy the `url` and `Authorization` header from the configuration.
- Package inspection, store queries, update previews, asset preparation, and workbook validation are available when the service is enabled. Enable submission, draft operations, and screenshot installation to use those tools. Store queries and previews can reuse a desktop session or use `APPSTORE_USERNAME` and `APPSTORE_PASSWORD` in the client environment.
- From a source checkout, `scripts/appstore-mcp` starts the server independently and uses the project `.venv` when available. Use `--transport http` or `--transport sse` for a local endpoint. Override the port with `--port` and set a Bearer token through `APPSTORE_MCP_HTTP_TOKEN` or `APPSTORE_MCP_SSE_TOKEN`. See the [stdio example configuration](appstore/examples/uos-ai-mcp.example.json).

#### Tools

- `inspect_packages` reads `.deb`, `.uab`, and `.layer` packages and distinguishes `loongarch64` from `loong64`. `sync_capabilities` refreshes the local store cache. `get_capabilities` lists system lines and selectable versions by package family and architecture.
- `list_my_apps` returns the store app ID (`app_id`) and revision ID (`detail_id`) for apps and drafts. `get_app_workflow` reads update history by app ID and review steps by revision ID. `get_app_history_notes` reads the original notes for a revision.
- To update a specific revision, pass its `detail_id` to `prepare_app_update` to preview changes, then pass the same edits to `submit_app_update`. Set `operation` to `submit_update` to submit an update, `save_draft` to save the original draft, or `submit_draft` to send it for review. `delete_draft` deletes the selected draft.
- `preprocess_assets` prepares icons and screenshots using the desktop rules. By default, `start_capture_packages` installs the app locally and captures screenshots in a virtual display. Pass its `job_id` to `get_job_status` for the status, screenshot paths, and report. `capture_packages` runs synchronously.
- Use `prepare_new_app_workbook` to generate a workbook from your actual packages. Fill in any missing fields and run `validate_workbook`. `upload_workbook` submits a new app or batch update, with `dry_run` available for validation. `generate_example_template` creates a LabelNova demo.
- `upload_packages` is the legacy package update workflow for an existing app matched by package name or `app_id`. `start_auto_upload_packages` captures screenshots from one package in the background and submits the packages with those screenshots once it has 3 to 6 valid images. Use its `job_id` to check the result. `auto_upload_packages` runs synchronously. Updates with captured screenshots use API mode.
- Batch submissions require store credentials. Set `APPSTORE_USERNAME` and `APPSTORE_PASSWORD` in the client environment.
- Default `sudo` install and uninstall commands use `APPSTORE_SUDO_PASSWORD` when set and fall back to `pkexec` otherwise. `click-text:<visible text>` uses OCR to click matching text in the captured window. Run `scripts/setup-ocr-venv` and point `APPSTORE_OCR_PYTHON` at `./.venv-ocr/bin/python3` to configure OCR.
