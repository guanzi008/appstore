from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from appstore.screenshot_validation import ScreenshotValidationReport, validate_screenshot_paths
from ui.package_meta import PackageGroup, extract_archive_icon, extract_deb_icon


MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024
DEFAULT_ICON_SIZE = 512
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
LANDSCAPE_SCREENSHOT_SIZE = (1050, 700, 1920, 1280)
PORTRAIT_SCREENSHOT_SIZE = (360, 640, 900, 1600)


@dataclass(frozen=True)
class AssetBundle:
    icon_source: Path | None
    screenshot_sources: tuple[Path, ...]
    icon_path: Path | None
    screenshot_paths: tuple[Path, ...]
    validation_report: ScreenshotValidationReport | None
    warnings: tuple[str, ...]


def detect_asset_candidates(
    package_group: PackageGroup,
    *,
    asset_dir: Path | None = None,
) -> tuple[Path | None, tuple[Path, ...]]:
    search_root = _resolve_asset_search_root(package_group, asset_dir=asset_dir)
    pkg_name = package_group.pkg_name
    icon = _detect_icon(search_root, pkg_name=pkg_name)
    screenshots = _detect_screenshots(search_root)
    return icon, screenshots


def preprocess_assets(
    package_group: PackageGroup,
    *,
    output_dir: Path,
    asset_dir: Path | None = None,
    manual_icon_path: Path | None = None,
    manual_screenshot_paths: tuple[Path, ...] | None = None,
    min_screenshots: int = 3,
    max_screenshots: int = 6,
) -> AssetBundle:
    output_dir = output_dir.expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="utpublisher-assets-") as temp_dir:
        temp_root = Path(temp_dir)
        if asset_dir is not None and output_dir.exists() and _path_is_within(asset_dir.expanduser().resolve(), output_dir):
            preexisting_icon, preexisting_screenshots = detect_asset_candidates(package_group, asset_dir=asset_dir)
            manual_icon_path = manual_icon_path or preexisting_icon
            if manual_screenshot_paths is None:
                manual_screenshot_paths = preexisting_screenshots

        manual_icon_path = _snapshot_asset(manual_icon_path, output_dir=output_dir, temp_root=temp_root)
        if manual_screenshot_paths is not None:
            manual_screenshot_paths = tuple(
                path
                for path in (
                    _snapshot_asset(path, output_dir=output_dir, temp_root=temp_root, prefix=f"screenshot-{index:02d}")
                    for index, path in enumerate(manual_screenshot_paths, start=1)
                )
                if path is not None
            )

        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        warnings: list[str] = []
        detected_icon, detected_screenshots = detect_asset_candidates(package_group, asset_dir=asset_dir)
        icon_source = manual_icon_path or detected_icon
        screenshot_sources = detected_screenshots if manual_screenshot_paths is None else manual_screenshot_paths

        processed_icon = None
        try:
            if icon_source is None:
                local_package = next((package for package in package_group.packages if package.path.is_file()), None)
                if local_package is not None:
                    extract_icon = extract_deb_icon if local_package.package_family == "deb" else extract_archive_icon
                    icon_source = extract_icon(
                        local_package.path,
                        pkg_name=package_group.pkg_name,
                        output_dir=output_dir / "extracted",
                    )
            if icon_source is not None:
                processed_icon = _prepare_icon(icon_source, output_dir / "icon.png")
            else:
                warnings.append("未找到应用图标，请手动选择图标文件。")
        except Exception as exc:
            warnings.append(f"图标处理失败。{exc}")

        if not screenshot_sources:
            warnings.append("未检测到截图，可手动指定资源目录或使用自动截图。")

        processed_sources: dict[Path, Path] = {}
        screenshots_dir = output_dir / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        for index, source in enumerate(screenshot_sources[: max_screenshots], start=1):
            try:
                processed = _prepare_screenshot(source, screenshots_dir / f"screen-{index:02d}")
                processed_sources[processed] = source
            except Exception as exc:
                warnings.append(f"截图处理失败 {source.name}: {exc}")

        validation_report = None
        processed_screenshots = ()
        if processed_sources:
            validation_report = validate_screenshot_paths(
                tuple(processed_sources),
                min_width=PORTRAIT_SCREENSHOT_SIZE[0],
                min_height=PORTRAIT_SCREENSHOT_SIZE[1],
                min_file_size=1,
                min_gray_stddev=2.5,
                min_unique_gray_levels=8,
            )
            processed_screenshots = validation_report.accepted_paths
            for item in validation_report.items:
                if item.accepted:
                    continue
                reasons = ", ".join(item.reasons) if item.reasons else "rejected"
                warnings.append(f"{processed_sources[item.analysis.path].name} 未通过校验。{reasons}")

        if len(processed_screenshots) < min_screenshots:
            warnings.append(
                f"有效截图不足：当前 {len(processed_screenshots)} 张，至少需要 {min_screenshots} 张。"
            )

        return AssetBundle(
            icon_source=icon_source,
            screenshot_sources=screenshot_sources,
            icon_path=processed_icon,
            screenshot_paths=processed_screenshots,
            validation_report=validation_report,
            warnings=tuple(warnings),
        )


def _common_parent(paths: tuple[Path, ...]) -> Path:
    if len(paths) == 1:
        return paths[0].parent
    return Path(os.path.commonpath([str(path.parent.resolve()) for path in paths]))


def _resolve_asset_search_root(package_group: PackageGroup, *, asset_dir: Path | None) -> Path:
    if asset_dir is None:
        return _common_parent(tuple(package.path for package in package_group.packages))
    normalized = asset_dir.expanduser().resolve()
    candidates = [
        normalized / package_group.pkg_name,
        normalized / package_group.display_name,
        normalized / package_group.display_name.replace(" ", "-"),
        normalized / package_group.display_name.replace(" ", "_"),
        normalized,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return normalized


def _detect_icon(search_root: Path, *, pkg_name: str) -> Path | None:
    candidates = (
        search_root / "icon.png",
        search_root / f"{pkg_name}.png",
        search_root / "assets" / "icon.png",
        search_root / "assets" / f"{pkg_name}.png",
    )
    for candidate in candidates:
        if _is_image_file(candidate):
            return candidate
    return None


def _detect_screenshots(search_root: Path) -> tuple[Path, ...]:
    directories = (search_root / "screenshots", search_root / "assets")
    named_candidates = (
        "screenshot_1.png",
        "screenshot_2.png",
        "screenshot_3.png",
        "shot-1.png",
        "shot-2.png",
        "shot-3.png",
    )
    for directory in directories:
        if not directory.exists() or not directory.is_dir():
            continue
        found_named = tuple(
            path
            for name in named_candidates
            if _is_image_file(path := directory / name)
        )
        if found_named:
            return found_named
        generic = tuple(
            path
            for path in sorted(directory.iterdir())
            if _is_image_file(path)
        )
        if generic:
            return generic
    return ()


def _is_image_file(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_SUFFIXES and path.is_file() and path.stat().st_size > 0


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _snapshot_asset(
    source: Path | None,
    *,
    output_dir: Path,
    temp_root: Path,
    prefix: str = "asset",
) -> Path | None:
    if source is None:
        return None
    source = source.expanduser().resolve()
    if not source.exists() or not source.is_file():
        return source
    if not _path_is_within(source, output_dir):
        return source
    suffix = source.suffix if source.suffix else ".bin"
    target = temp_root / f"{prefix}{suffix}"
    shutil.copy2(source, target)
    return target


def _prepare_icon(source: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    filters = (
        f"scale={DEFAULT_ICON_SIZE}:{DEFAULT_ICON_SIZE}:"
        "force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={DEFAULT_ICON_SIZE}:{DEFAULT_ICON_SIZE}:(ow-iw)/2:(oh-ih)/2:"
        "color=black@0,format=rgba"
    )
    _run_ffmpeg_image_convert(source, target, filters=filters)
    return target


def _prepare_screenshot(source: Path, target_base: Path) -> Path:
    filters = _screenshot_normalize_filters(source)
    png_path = target_base.with_suffix(".png")
    png_path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg_image_convert(source, png_path, filters=filters)
    if png_path.stat().st_size <= MAX_SCREENSHOT_BYTES:
        return png_path

    jpg_path = target_base.with_suffix(".jpg")
    for quality in (2, 4, 6, 8, 10, 12, 14, 16):
        _run_ffmpeg_image_convert(
            source,
            jpg_path,
            filters=f"{filters},format=yuvj420p",
            quality=quality,
        )
        if jpg_path.stat().st_size <= MAX_SCREENSHOT_BYTES:
            png_path.unlink(missing_ok=True)
            return jpg_path
    png_path.unlink(missing_ok=True)
    jpg_path.unlink(missing_ok=True)
    raise RuntimeError("截图压缩后仍超过 2 MiB。")


def _screenshot_normalize_filters(source: Path) -> str:
    width, height = _probe_dimensions(source)
    if height > width:
        min_width, _, max_width, _ = PORTRAIT_SCREENSHOT_SIZE
        width_step, height_step = 18, 32
    else:
        min_width, _, max_width, _ = LANDSCAPE_SCREENSHOT_SIZE
        width_step, height_step = 6, 4

    # Use even dimensions at the required aspect ratio for both PNG and JPEG.
    requested_units = max(
        (width + width_step - 1) // width_step,
        (height + height_step - 1) // height_step,
    )
    units = max(min_width // width_step, min(max_width // width_step, requested_units))
    target_width, target_height = units * width_step, units * height_step
    return (
        f"scale={target_width}:{target_height}:"
        "force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        "setsar=1"
    )


def _probe_dimensions(path: Path) -> tuple[int, int]:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-read_intervals",
                "%+#1",
                "-show_entries",
                "stream=width,height:stream_side_data=rotation:frame=:frame_side_data=rotation",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ffprobe not found. Install ffmpeg to preprocess assets.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"ffprobe failed for {path.name}: {detail}") from exc

    try:
        details = json.loads(completed.stdout)
        stream = details["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
        frame = (details.get("frames") or [{}])[0]
        side_data = frame.get("side_data_list", []) + stream.get("side_data_list", [])
        rotation = next((round(float(item["rotation"])) for item in side_data if "rotation" in item), 0)
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"ffprobe returned invalid dimensions for {path.name}") from exc
    if width <= 0 or height <= 0:
        raise RuntimeError(f"invalid image dimensions for {path.name}: {width}x{height}")
    return (height, width) if rotation % 180 == 90 else (width, height)


def _run_ffmpeg_image_convert(
    source: Path,
    target: Path,
    *,
    filters: str,
    quality: int | None = None,
) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        filters,
    ]
    if quality is not None:
        command.extend(["-q:v", str(quality)])
    command.append(str(target))
    try:
        subprocess.run(command, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg not found. Install ffmpeg to preprocess assets.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"ffmpeg failed for {source.name}: {detail}") from exc
    if not target.exists() or target.stat().st_size <= 0:
        raise RuntimeError(f"failed to save image: {target}")
