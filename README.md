# 统信应用投递助手

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

UOS AI MCP:

- MCP server entry: `scripts/appstore-mcp`
- Example config: [appstore/examples/uos-ai-mcp.example.json](appstore/examples/uos-ai-mcp.example.json)
- Key tools:
  - `prepare_new_app_workbook`: build a real submission workbook from actual packages
  - `upload_workbook`: submit a new app or batch update from a workbook
  - `upload_packages` / `auto_upload_packages`: existing-app update only
  - `generate_example_template`: LabelNova demo workbook only, not for real submissions
- Privilege handling:
  - You can set `APPSTORE_SUDO_PASSWORD` in the MCP JSON `env`
  - If it is missing, default `sudo ...` install/uninstall commands automatically fall back to `pkexec ...`
- OCR handling:
  - `click-text:<visible text>` steps use OCR to click visible labels in the captured window
  - Install OCR runtime into `./.venv-ocr` with `scripts/setup-ocr-venv`
  - Point `APPSTORE_OCR_PYTHON` at `./.venv-ocr/bin/python3` in the MCP JSON `env`
