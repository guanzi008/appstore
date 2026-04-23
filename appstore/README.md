# App Store Batch Upload

Chinese usage guide: [README.zh-CN.md](./README.zh-CN.md)

Install dependencies with:

```bash
python3 -m pip install -r appstore/requirements.txt
```

Generate the example workbook and example files with:

```bash
python3 -m appstore.upload_batch generate-template --capabilities-cache appstore/cache/capabilities
```

This creates `appstore/examples/template.xlsx` plus example assets under `appstore/examples/assets/` and bundled example Debian packages under `appstore/examples/packages/`.

The shipped example workbook is intended for dry-run validation. It now demonstrates a real multi-architecture `labelnova` release with bundled `amd64`, `arm64`, and `loong64` `.deb` files from OBS. The package sheet no longer asks you to type raw `sup_sys_code` or `baseline_id` values directly. Instead, `generate-template` expands the latest capability cache into groups of store-aligned system columns and a companion `system_templates` reference sheet. Before a real upload, replace the example `.deb` and image assets with your actual files, then rerun `generate-template` after `sync-capabilities` so the template columns reflect the latest store data.

The workbook has four sheets:

- `apps`: `app_key`, `app_name_zh`, `pkg_name`, `category_id`, `website`, `short_desc_zh`, `full_desc_zh`, `icon_path`, `screenshot_1`, `screenshot_2`, `screenshot_3`, `keywords_zh`, `app_id_override`
- `releases`: `enabled`, `app_key`, `release_key`, `execution_mode`, `region`, `note`
- `packages`: `enabled`, `app_key`, `release_key`, `package_key`, `file_path`, `pkg_channel`, `note`, plus generated `sys__...__enabled`, `sys__...__baseline`, and `sys__...__unsupported` columns
- `system_templates`: generated mapping from each `sys__...` system group to the real package family, system line, and concrete baseline/version candidates from the store capability cache

`packages` is the user-facing target selection layer. `load_manifest()` expands each `sys__...__enabled/baseline/unsupported` group into internal targets automatically, so the grouped CLI still validates and submits real `sup_sys_code`, `baseline_id`, and `unsupportBaseline` values without exposing those raw codes as the primary editing surface. The bundled workbook shows one release with multiple package rows, which is the expected way to upload the same app version across multiple architectures.

`execution_mode` controls how a release is uploaded:

- empty or `auto`: use the CLI default
- `api`: use the API-driven submission path; if the app already exists, the tool now fetches app detail first and reuses the existing icon, screenshots, category, and localized texts
- `browser`: use the browser-backed fallback path with staged page saves

The browser flow remains useful, but it is now the fallback mode. The recommended default is `api`, because it models the real UnionTech portal request flow directly and only falls back to browser automation when a specific UI-only step is still unresolved.

Grouped CLI commands:

- `sync-capabilities`: logs in, fetches capability data, and writes the cache to disk
- `validate`: loads the workbook, capabilities cache, and package metadata, then writes `validated` or `validate_failed` report rows
- `upload`: runs grouped validation first, supports `--dry-run`, and submits grouped releases on real runs
- `upload-packages`: skips the workbook, inspects one or more package files directly, resolves the existing store app by package name or `--app-id`, and submits an update
- `generate-template`: writes the example workbook and supporting files

`upload` now supports hybrid execution flags:

- `--mode auto|api|browser`
- `--session-cache-dir appstore/cache/session-state`
- `--artifact-dir <dir>`
- `--headless` / `--no-headless`

Row selection is grouped-aware. Plain integer selectors refer to package rows by default. Use `r:<row_id>` to replay an empty-release failure row when a release has no package rows.

Credentials resolve in this order for `sync-capabilities` and `upload`:

1. `APPSTORE_USERNAME`
2. `APPSTORE_PASSWORD`
3. Interactive prompts if either value is missing

`--dry-run` never prompts and uses empty credentials.

Run `sync-capabilities` before `validate` or `upload` if you do not already have a local capability cache. `validate` and `upload` both need a readable cache directory from `--capabilities-cache`.

Dry-run validation example:

```bash
python3 -m appstore.upload_batch validate appstore/examples/template.xlsx --rows 2
```

Real upload example:

```bash
# Replace the example package/assets in the example workbook first,
# or create a workbook that points to your real files.
APPSTORE_USERNAME='your-username' APPSTORE_PASSWORD='your-password' \
python3 -m appstore.upload_batch upload appstore/examples/template.xlsx --rows 2 --mode api
```

Direct package update example:

```bash
APPSTORE_USERNAME='your-username' APPSTORE_PASSWORD='your-password' \
python3 -m appstore.upload_batch upload-packages \
  /path/to/app_1.0.5_amd64.deb \
  /path/to/app_1.0.5_arm64.deb \
  --app-id 1096227 \
  --note 'Add arm64 package and refresh update notes' \
  --mode api
```

`upload-packages` is designed for existing app updates. It reads `pkg_name`, `pkg_version`, and `pkg_arch` from the package files, reuses the current store metadata from app detail, and only replaces the update description, uploaded packages, and system-target data.

For an existing app update, set `apps.app_id_override` when you know the store app id. In that case `upload --mode api` will reuse the existing store metadata automatically, so in practice you usually only need to update:

- `releases.note` for the new update description
- the package files
- the generated `sys__...__enabled/baseline/unsupported` columns

Browser-backed upload example:

```bash
APPSTORE_USERNAME='your-username' APPSTORE_PASSWORD='your-password' \
python3 -m appstore.upload_batch upload appstore/examples/template.xlsx --rows 2 --mode browser
```

Sync capability cache example:

```bash
APPSTORE_USERNAME='your-username' APPSTORE_PASSWORD='your-password' \
python3 -m appstore.upload_batch sync-capabilities
```

Each run writes reports under `appstore/output/<timestamp>/`:

- `report.json`
- `report.xlsx`

Browser-backed runs also write browser artifacts under the chosen artifact directory, including the final browser submission result JSON.
