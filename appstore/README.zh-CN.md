# 应用商店批量上传工具使用说明

对应英文说明见：[README.md](./README.md)

## 现在什么能用

当前 `appstore` 目录下已经能稳定使用的入口是：

- `python3 -m appstore.upload_batch sync-capabilities`
- `python3 -m appstore.upload_batch validate`
- `python3 -m appstore.upload_batch upload --mode api`
- `python3 -m appstore.upload_batch upload --mode browser`
- `python3 -m appstore.upload_batch upload-packages --mode api`
- `python3 -m appstore.upload_batch generate-template`

当前推荐理解为：

- `api` 模式：主路径。会尽量按真实商店抓包出来的接口行为提交，已有应用更新时会先拉取应用详情并复用现有图标、截图、分类和文案
- `browser` 模式：兜底路径。走真实商店页面上传/更新，适合某一步接口还没打通或页面行为明显更稳的场景
- `auto` 模式：当前按保守策略处理，实际等价于优先走 `api`

另外现在多了一条最省事的路径：

- `upload-packages`
  - 不需要 `xlsx`
  - 直接解析包文件里的 `pkg_name/pkg_version/arch`
  - 自动查找现有应用并做更新
  - 默认复用商店现有图标、截图、分类、简介和详情
  - 你主要只需要提供：包文件和更新说明

## 什么时候用哪个

### 用 `api`

适合这些情况：

- 新应用首次提审
- 普通单包或常规多包提审
- 已有应用更新，只想改“更新内容 + 新包 + 系统版本”
- 不依赖页面特殊行为

命令示例：

```bash
APPSTORE_USERNAME='你的账号' APPSTORE_PASSWORD='你的密码' \
python3 -m appstore.upload_batch upload your.xlsx --mode api
```

### 用 `upload-packages`

适合这些情况：

- 已有应用更新
- 不想再维护 `xlsx`
- 手上已经有编好的一个或多个包
- 只想“解析包名 -> 命中现有应用 -> 提交更新”

命令示例：

```bash
APPSTORE_USERNAME='你的账号' APPSTORE_PASSWORD='你的密码' \
python3 -m appstore.upload_batch upload-packages \
  /path/to/app_1.0.5_amd64.deb \
  /path/to/app_1.0.5_arm64.deb \
  --app-id 1096227 \
  --note '新增 arm64 包并更新版本说明' \
  --mode api
```

说明：

- `pkg_name/pkg_version/arch` 直接从包里读取
- `app_id` 已知时建议显式传 `--app-id`
- 如果不传 `--app-id`，工具会用包里的 `pkg_name` 去商店里找现有应用
- 这个入口当前面向“已有应用更新”，不是新应用首发入口

### 用 `browser`

适合这些情况：

- 某一步接口行为和页面行为还没完全对齐
- 商店页面流程已经验证过比直接接口更稳
- 需要强行复现页面分阶段保存

当前这条路径已经按真实商店行为做了正式整合，尤其适合：

- 已存在应用
- 通过 `pkg_name` 或 `app_id_override` 能唯一定位到应用
- 需要复用页面登录态和系统版本管理逻辑

命令示例：

```bash
APPSTORE_USERNAME='你的账号' APPSTORE_PASSWORD='你的密码' \
python3 -m appstore.upload_batch upload your.xlsx --mode browser
```

建议：

- 默认先用 `api`
- 如果是更新已有应用，最好在 `apps` sheet 里填写 `app_id_override`
- 只有在某一步接口模拟还不稳定时，再切 `browser`

## 最小使用流程

### 1. 安装依赖

```bash
python3 -m pip install -r appstore/requirements.txt
```

### 2. 先同步商店能力缓存

`generate-template`、`validate` 和 `upload` 都依赖能力缓存。第一次使用时，先跑：

```bash
APPSTORE_USERNAME='你的账号' APPSTORE_PASSWORD='你的密码' \
python3 -m appstore.upload_batch sync-capabilities
```

默认缓存目录：

- `appstore/cache/capabilities`

### 3. 再生成模板

```bash
python3 -m appstore.upload_batch generate-template --capabilities-cache appstore/cache/capabilities
```

默认会生成：

- `appstore/examples/template.xlsx`
- `appstore/examples/assets/`
- `appstore/examples/packages/`

现在模板里的“系统模板列”来自最新 capability cache，不再要求你手填 `sup_sys_code` / `baseline_id`；而且每个系统线会带出具体版本候选。

如果你不想用 `xlsx`，可以直接跳过这一步，改用上面的 `upload-packages`。

### 4. 修改 Excel 清单

工作簿有 4 个 sheet：

- `apps`
- `releases`
- `packages`
- `system_templates`

关键字段：

- `releases.execution_mode`
  - 留空或 `auto`
  - `api`
  - `browser`
- `apps.app_id_override`
  - 已知应用 `app_id` 时建议填写
  - `api` 模式下如果命中已有应用，会自动拉取当前应用 detail，复用现有图标、截图、分类、简介和详情
  - 这时通常只需要改 `releases.note`、包文件和系统版本勾选
- `packages` 里自动生成的 `sys__...` 列组
  - `...__enabled`：是否启用这个系统线
  - `...__baseline`：兼容应用基线，填具体版本
  - `...__unsupported`：不上架版本，填具体版本列表
  - 具体候选看 `system_templates` sheet，不再手填系统代码

### 5. 本地校验

```bash
python3 -m appstore.upload_batch validate your.xlsx --capabilities-cache appstore/cache/capabilities
```

只校验部分行时可以加：

```bash
python3 -m appstore.upload_batch validate your.xlsx --rows 2
python3 -m appstore.upload_batch validate your.xlsx --rows p:2,p:3
python3 -m appstore.upload_batch validate your.xlsx --rows r:2
```

### 6. 正式上传

接口模式：

```bash
APPSTORE_USERNAME='你的账号' APPSTORE_PASSWORD='你的密码' \
python3 -m appstore.upload_batch upload your.xlsx --mode api
```

浏览器模式：

```bash
APPSTORE_USERNAME='你的账号' APPSTORE_PASSWORD='你的密码' \
python3 -m appstore.upload_batch upload your.xlsx --mode browser
```

只做预演不实际提交：

```bash
python3 -m appstore.upload_batch upload your.xlsx --dry-run --capabilities-cache appstore/cache/capabilities
```

## Excel 结构说明

这一部分按“当前工具实际行为”来解释，不按想象解释。

先说最重要的结论：

- Excel 里有一部分字段是“本地分组/控制字段”，不会直接提交到 `appstore-dev`
- 真正提交到平台的很多字段，是工具根据包文件、能力缓存和上传结果自动生成的
- 当前工具已经对齐到平台的主要结构：`应用资料 + 适配信息 + 包列表`
- 但并不是 Excel 每一列都会一比一进入平台 payload

平台实际提交时，当前工具主要会组装这些块：

- `app_info.app_lan_infos`
- `app_info.app_basic_info`
- `app_info.app_fit_info`
- `app_info.app_origin_pkgs`

下面按 sheet 说明。

### `apps` sheet

一行表示一个应用。它主要对应平台的应用级资料。

| Excel 列 | 是否建议填写 | 实际作用 | 对应平台字段 | 说明 |
| --- | --- | --- | --- | --- |
| `app_key` | 必填 | 本地主键 | 不直接提交 | 只是用来给 `releases/packages` 关联 |
| `app_name_zh` | 必填 | 应用中文名 | `app_lan_infos[].name` | 真正提交 |
| `pkg_name` | 必填 | 应用包名 | 用于校验和查找现有应用 | 不是直接从这里写入 payload，而是会和包内元数据校验 |
| `category_id` | 必填 | 商店分类 | `app_basic_info.category_id` | 真正提交 |
| `website` | 必填 | 官网/产品页 | `app_basic_info.website` | 真正提交 |
| `short_desc_zh` | 必填 | 简介 | `app_lan_infos[].brief_info` | 真正提交 |
| `full_desc_zh` | 必填 | 详情 | `app_lan_infos[].desc_info` | 真正提交 |
| `icon_path` | 新应用必填 | 本地图标路径 | 上传后变成 `icon_save_key` | 新应用或需要替换图标时使用；已有应用更新默认复用商店现有图标 |
| `screenshot_1` | 新应用必填 | 第 1 张截图 | 上传后变成 `appScreenShotList[0]` | 新应用或需要替换截图时使用；已有应用更新默认复用商店现有截图 |
| `screenshot_2` | 新应用必填 | 第 2 张截图 | 上传后变成 `appScreenShotList[1]` | 当前工具要求至少 3 张 |
| `screenshot_3` | 新应用必填 | 第 3 张截图 | 上传后变成 `appScreenShotList[2]` | 当前工具要求至少 3 张 |
| `keywords_zh` | 可选 | 预留字段 | 当前未提交 | 目前工具保存但没有真正写进平台 payload |
| `app_id_override` | 强烈建议已有应用时填写 | 强制命中已有应用 | 不直接提交 | 用于“更新哪个现有应用”，不是平台业务字段 |

补充说明：

- 平台截图规则你已经确认过：`jpg/png`，单张 `<= 2MB`，数量 `3-6` 张
- 但当前工具版本仍固定读取 `screenshot_1~3` 三张，不支持 `4-6` 张
- 也就是说：平台允许 `3-6` 张，但当前工具实际只支持 `3` 张

### `releases` sheet

一行表示一次发布分组。它主要控制“这批包怎么一起处理”，不是直接对应平台页面上的某一个单独实体。

| Excel 列 | 是否建议填写 | 实际作用 | 对应平台字段 | 说明 |
| --- | --- | --- | --- | --- |
| `enabled` | 必填 | 是否启用该行 | 不直接提交 | 本地控制字段 |
| `app_key` | 必填 | 关联 `apps` | 不直接提交 | 本地分组字段 |
| `release_key` | 必填 | 发布分组键 | 不直接提交 | 本地分组字段，同一应用内唯一 |
| `execution_mode` | 可选 | 控制走 `api/browser/auto` | 不直接提交 | 纯本地控制字段 |
| `region` | 建议填写 | 区域码 | `app_basic_info.region` / `app_fit_info.region` | 当前通常填 `1` |
| `note` | 可选 | 备注/更新说明 | 更新时会写到 `app_lan_infos[].update_desc` | 这就是“更新包只改更新内容就提交”里最重要的用户输入 |

你可以把 `releases` 理解成：

- `apps` 是“应用”
- `releases` 是“这次准备提交的一批内容”
- `packages` 是“这批内容里的具体包文件”
- `system_templates` 是“`packages` 里每组 `sys__...` 列到底代表哪个系统线，以及有哪些具体版本候选”

### `packages` sheet

一行表示一个实际上传的包文件。平台真正关心的很多字段会从包里自动读，不是让你手填。系统选择也不再单独放到 `targets` 里，而是直接在这一行横向填写系统线勾选、兼容基线和不上架版本。

| Excel 列 | 是否建议填写 | 实际作用 | 对应平台字段 | 说明 |
| --- | --- | --- | --- | --- |
| `enabled` | 必填 | 是否启用该包 | 不直接提交 | 本地控制字段 |
| `app_key` | 必填 | 关联应用 | 不直接提交 | 本地关联字段 |
| `release_key` | 必填 | 关联发布组 | 不直接提交 | 本地关联字段 |
| `package_key` | 必填 | 包唯一键 | 不直接提交 | 本地关联键 |
| `file_path` | 必填 | 本地包路径 | 上传后生成 `file_save_key` | 这是最核心的实际输入 |
| `pkg_channel` | 可选 | 发布通道 | `app_origin_pkgs[].pkgChannel` | 会真实提交 |
| `note` | 可选 | 备注 | 当前不直接提交 | 本地说明字段 |
| `sys__...__enabled` | 按需勾选 | 是否启用这个系统线 | 最终会展开成 `supSys/system_platform` | 对应商店里的勾选框 |
| `sys__...__baseline` | 按需填写 | 兼容应用基线 | 最终会展开成 `baseline/supBlineVer` | 填具体版本，候选见 `system_templates` |
| `sys__...__unsupported` | 按需填写 | 不上架版本 | 最终会展开成 `unsupportBaseline/unsupportBlineVers` | 多个版本用逗号分隔 |

下面这些值不要手填，因为当前工具会自动从包文件解析：

- `package_family`
- `package_format`
- `pkg_name`
- `pkg_version`
- `pkg_arch`
- `pkg_size`
- `sha256`

这些值最终会进入平台的：

- `app_origin_pkgs[].pkg_name`
- `app_origin_pkgs[].pkg_version`
- `app_origin_pkgs[].pkg_arch`
- `app_origin_pkgs[].pkgArch`
- `app_origin_pkgs[].pkg_size`
- `app_origin_pkgs[].sha256`

其中：

- `pkg_arch` / `pkgArch` 会自动映射成平台代码和显示标签
- `pkgType` 会根据包文件后缀自动生成
- `file_save_key` 会在实际上传文件后自动生成

### `system_templates` sheet

这个 sheet 是工具自动生成的说明页，不需要你手填。它会把 `packages` 里的每一组 `sys__...` 列解释清楚。

| Excel 列 | 是否建议填写 | 实际作用 | 对应平台字段 | 说明 |
| --- | --- | --- | --- | --- |
| `column_prefix` | 自动生成 | 对应 `packages` 里的系统列前缀 | 不直接提交 | 例如 `sys__deb__11` |
| `package_family` | 自动生成 | 这个模板适用于哪种包 | 影响 `pkgInstallMode/pkgType` | `deb` 或 `linglong` |
| `system_label` | 自动生成 | 系统线展示名 | 对应平台系统线 | 来自 capability cache |
| `sup_sys_code` | 自动生成 | 系统线原始代码 | `system_platform` / `supSys` | 不再需要你手填 |
| `baseline_options` | 自动生成 | 这个系统线下的具体版本候选 | 对应平台版本列表 | 例如 `2300:23.0.0, 2301:23.0.1` |

也就是说：

- 你真正编辑的是 `packages` 里的勾选列
- `system_templates` 只负责告诉你“这个系统线有哪些具体版本可以选”
- 最终内部仍然会展开成真实的 `sup_sys_code / baseline_id`

### 哪些字段是“自动的”，不需要你填

下面这些不是 Excel 手填字段，而是工具运行时自动生成或自动推导：

- `app_id`
  - 来自 `app_id_override` 或平台搜索命中
- `icon_save_key`
  - 图标上传后返回
- `screen_shot_key`
  - 截图上传后返回
- `file_save_key`
  - 包上传后返回
- `pkg_name/pkg_version/pkg_arch/pkg_size/sha256`
  - 从包文件自动解析
- `pkgType`
  - 由包文件类型自动映射
- `pkgArch`
  - 由包架构自动映射
- `app_fit_info.system_platform`
  - 由所有启用的系统线汇总生成
- `app_fit_info.arch`
  - 由所有包自动汇总生成
- `app_fit_info.baseline`
  - 由所有填写的兼容应用基线自动汇总生成
- `unsupportBaseline / unsupportBlineVers`
  - 由所有填写的不上架版本自动汇总生成
- `systemStr`
  - 由系统线和具体版本自动拼装
- `upload_time`
  - 提交时自动生成
- `progressPercent`
  - 当前工具固定按上传完成写入

### 哪些字段只是本地控制，不是平台字段

这些字段主要为了让工具能批量处理，不是平台真实业务字段：

- `app_key`
- `release_key`
- `package_key`
- `enabled`
- `execution_mode`
- `app_id_override`
- `note`

其中只有 `note` 在浏览器模式里可能被用作更新说明文本，其他都主要是本地控制用途。

### 当前工具和平台的差异

这部分你要特别知道：

- 平台允许 `3-6` 张截图，但当前工具只支持固定 `3` 张
- 平台页面里会展示更多文案和状态，但当前工具只提交必要字段
- `keywords_zh` 现在保留在 workbook 中，但当前工具还没有真正提交到平台
- `release_name` 已经不是用户模板必填字段，工具会按 `release_key` 兜底
- 系统模板列和具体版本候选都来自 `sync-capabilities` 的最新缓存，不建议手工长期维护旧模板
- 浏览器模式是“真实页面驱动”，更贴近平台实际行为；接口模式是“结构化提交”，更适合标准化批量处理

## 行选择器说明

`validate` 和 `upload` 的 `--rows` 是分组感知的：

- 裸数字默认按 `package` 行处理
- `p:20` 明确指定 package 行
- `r:20` 明确指定 release 行

示例：

```bash
python3 -m appstore.upload_batch upload your.xlsx --rows 20 --mode api
python3 -m appstore.upload_batch upload your.xlsx --rows p:20,p:21 --mode browser
python3 -m appstore.upload_batch validate your.xlsx --rows r:20
```

## 浏览器模式的补充说明

浏览器模式有这些特性：

- 复用登录态缓存
- 默认缓存目录：`appstore/cache/session-state`
- 支持 `--headless` 和 `--no-headless`
- 支持 `--artifact-dir`

示例：

```bash
APPSTORE_USERNAME='你的账号' APPSTORE_PASSWORD='你的密码' \
python3 -m appstore.upload_batch upload your.xlsx \
  --mode browser \
  --session-cache-dir appstore/cache/session-state \
  --artifact-dir /tmp/appstore-browser-run \
  --no-headless
```

注意：

- 浏览器模式依赖真实页面结构，适合“更新已有应用”
- 如果应用无法通过 `pkg_name` 唯一定位，最好填写 `app_id_override`
- 浏览器模式的输出除了普通报告，还会写浏览器产物

## 输出结果在哪里

每次运行默认写到：

- `appstore/output/<timestamp>/`

普通输出：

- `report.json`
- `report.xlsx`

浏览器模式还会额外写：

- 浏览器提交结果
- 调试产物目录

## 当前最推荐的真实用法

如果你现在是“已有应用更新，尤其是多架构 `deb` 更新”，建议用：

1. `sync-capabilities`
2. `validate`
3. `upload --mode browser`

如果你现在是“普通提审或新应用提审”，建议先用：

1. `sync-capabilities`
2. `validate`
3. `upload --mode api`
