from __future__ import annotations

import os
import shutil
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
    manual_screenshot_paths: tuple[Path, ...] = (),
    min_screenshots: int = 3,
    max_screenshots: int = 6,
) -> AssetBundle:
    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    detected_icon, detected_screenshots = detect_asset_candidates(package_group, asset_dir=asset_dir)
    icon_source = manual_icon_path or detected_icon
    screenshot_sources = tuple(manual_screenshot_paths or detected_screenshots)

    if icon_source is None:
        extracted_dir = output_dir / "extracted"
        first_package = package_group.packages[0]
        if first_package.package_family == "deb":
            icon_source = extract_deb_icon(
                first_package.path,
                pkg_name=package_group.pkg_name,
                output_dir=extracted_dir,
            )
        else:
            icon_source = extract_archive_icon(
                first_package.path,
                pkg_name=package_group.pkg_name,
                output_dir=extracted_dir,
            )
    if icon_source is None:
        warnings.append("未能自动探测到图标，请手动指定图标文件。")

    if not screenshot_sources:
        warnings.append("未检测到截图，可手动指定资源目录或使用自动截图。")

    processed_icon = _prepare_icon(icon_source, output_dir / "icon.png") if icon_source is not None else None

    processed_screenshots: list[Path] = []
    screenshots_dir = output_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(screenshot_sources[: max_screenshots], start=1):
        try:
            processed_screenshots.append(
                _prepare_screenshot(source, screenshots_dir / f"screen-{index:02d}")
            )
        except Exception as exc:
            warnings.append(f"截图处理失败 {source.name}: {exc}")

    validation_report = None
    if processed_screenshots:
        validation_report = validate_screenshot_paths(
            tuple(processed_screenshots),
            min_width=640,
            min_height=360,
            min_file_size=4096,
            min_gray_stddev=2.5,
            min_unique_gray_levels=8,
        )
        processed_screenshots = list(validation_report.accepted_paths)
        for item in validation_report.items:
            if item.accepted:
                continue
            reasons = ", ".join(item.reasons) if item.reasons else "rejected"
            warnings.append(f"{item.analysis.path.name} 被拒绝: {reasons}")

    if len(processed_screenshots) < min_screenshots:
        warnings.append(
            f"有效截图不足：当前 {len(processed_screenshots)} 张，至少需要 {min_screenshots} 张。"
        )

    return AssetBundle(
        icon_source=icon_source,
        screenshot_sources=screenshot_sources,
        icon_path=processed_icon,
        screenshot_paths=tuple(processed_screenshots),
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
    return path.exists() and path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES and path.stat().st_size > 1024


def _prepare_icon(source: Path, target: Path) -> Path:
    QtCore, QtGui, keep_aspect_ratio, smooth_transformation = _qt_image_api()
    image = _load_image(source)
    square_size = max(image.width(), image.height(), DEFAULT_ICON_SIZE)
    canvas = QtGui.QImage(
        square_size,
        square_size,
        QtGui.QImage.Format.Format_ARGB32 if hasattr(QtGui.QImage, "Format") else QtGui.QImage.Format_ARGB32,
    )
    canvas.fill(QtCore.Qt.GlobalColor.transparent if hasattr(QtCore.Qt, "GlobalColor") else 0)
    painter = QtGui.QPainter(canvas)
    x = (square_size - image.width()) // 2
    y = (square_size - image.height()) // 2
    painter.drawImage(x, y, image)
    painter.end()
    normalized = canvas.scaled(
        DEFAULT_ICON_SIZE,
        DEFAULT_ICON_SIZE,
        keep_aspect_ratio,
        smooth_transformation,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if not normalized.save(str(target), "PNG"):
        raise RuntimeError(f"failed to save icon: {target}")
    return target


def _prepare_screenshot(source: Path, target_base: Path) -> Path:
    image = _load_image(source)
    image = _normalize_screenshot_image(image)
    png_path = target_base.with_suffix(".png")
    png_path.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(png_path), "PNG"):
        raise RuntimeError(f"failed to save screenshot: {png_path}")
    if png_path.stat().st_size <= MAX_SCREENSHOT_BYTES:
        return png_path

    jpg_path = target_base.with_suffix(".jpg")
    for quality in (92, 88, 84, 80, 76, 72, 68, 64):
        if image.save(str(jpg_path), "JPG", quality) and jpg_path.stat().st_size <= MAX_SCREENSHOT_BYTES:
            png_path.unlink(missing_ok=True)
            return jpg_path
    return jpg_path if jpg_path.exists() else png_path


def _normalize_screenshot_image(image):
    QtCore, _, _, smooth_transformation = _qt_image_api()
    if image.width() <= 0 or image.height() <= 0:
        raise RuntimeError("invalid screenshot dimensions")

    if image.height() > image.width():
        min_width, min_height, max_width, max_height = PORTRAIT_SCREENSHOT_SIZE
        ratio_width, ratio_height = 9, 16
    else:
        min_width, min_height, max_width, max_height = LANDSCAPE_SCREENSHOT_SIZE
        ratio_width, ratio_height = 3, 2

    cropped = _center_crop_to_ratio(image, ratio_width, ratio_height)
    target_width, target_height = _bounded_size_for_ratio(
        cropped.width(),
        cropped.height(),
        min_width,
        min_height,
        max_width,
        max_height,
        ratio_width,
        ratio_height,
    )
    if cropped.width() == target_width and cropped.height() == target_height:
        return cropped

    ignore_aspect_ratio = (
        QtCore.Qt.AspectRatioMode.IgnoreAspectRatio
        if hasattr(QtCore.Qt, "AspectRatioMode")
        else QtCore.Qt.IgnoreAspectRatio
    )
    return cropped.scaled(target_width, target_height, ignore_aspect_ratio, smooth_transformation)


def _center_crop_to_ratio(image, ratio_width: int, ratio_height: int):
    width = image.width()
    height = image.height()
    target_ratio = ratio_width / ratio_height
    current_ratio = width / height
    if current_ratio > target_ratio:
        crop_width = max(1, round(height * target_ratio))
        x = max(0, (width - crop_width) // 2)
        return image.copy(x, 0, crop_width, height)
    if current_ratio < target_ratio:
        crop_height = max(1, round(width / target_ratio))
        y = max(0, (height - crop_height) // 2)
        return image.copy(0, y, width, crop_height)
    return image


def _bounded_size_for_ratio(
    width: int,
    height: int,
    min_width: int,
    min_height: int,
    max_width: int,
    max_height: int,
    ratio_width: int,
    ratio_height: int,
) -> tuple[int, int]:
    scale = 1.0
    if width > max_width or height > max_height:
        scale = min(max_width / width, max_height / height)
    if width * scale < min_width or height * scale < min_height:
        scale = max(scale, min_width / width, min_height / height)

    target_width = max(min_width, min(max_width, round(width * scale)))
    target_height = round(target_width * ratio_height / ratio_width)
    if target_height > max_height:
        target_height = max_height
        target_width = round(target_height * ratio_width / ratio_height)
    if target_height < min_height:
        target_height = min_height
        target_width = round(target_height * ratio_width / ratio_height)
    target_width = max(min_width, min(max_width, target_width))
    target_height = max(min_height, min(max_height, target_height))
    return target_width, target_height


def _load_image(path: Path):
    _, QtGui, _, _ = _qt_image_api()
    image = QtGui.QImage(str(path))
    if image.isNull():
        raise RuntimeError(f"failed to load image: {path}")
    return image


def _qt_image_api():
    from ui.qt_compat import KEEP_ASPECT_RATIO, QtCore, QtGui, SMOOTH_TRANSFORMATION

    return QtCore, QtGui, KEEP_ASPECT_RATIO, SMOOTH_TRANSFORMATION
