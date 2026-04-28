# appstore

Standalone project extracted from `/home/hao/Documents/ai/WWW/appstore`.

Quick start:

```bash
cd /home/hao/Documents/ai/appstore
python3 -m pip install -r requirements.txt
python3 -m appstore.upload_batch --help
```

Docs:

- Chinese: [appstore/README.zh-CN.md](appstore/README.zh-CN.md)
- English: [appstore/README.md](appstore/README.md)

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
