from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from appstore.screenshot_validation import ScreenshotValidationReport, validate_screenshot_paths
from ui.package_meta import PackageGroup, extract_archive_icon, extract_deb_icon
from ui.qt_compat import (
    KEEP_ASPECT_RATIO,
    QtCore,
    QtGui,
    SMOOTH_TRANSFORMATION,
)


MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024
DEFAULT_ICON_SIZE = 512
DEFAULT_SCREENSHOT_WIDTH = 1280
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


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
        KEEP_ASPECT_RATIO,
        SMOOTH_TRANSFORMATION,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if not normalized.save(str(target), "PNG"):
        raise RuntimeError(f"failed to save icon: {target}")
    return target


def _prepare_screenshot(source: Path, target_base: Path) -> Path:
    image = _load_image(source)
    if image.width() > DEFAULT_SCREENSHOT_WIDTH:
        image = image.scaledToWidth(DEFAULT_SCREENSHOT_WIDTH, SMOOTH_TRANSFORMATION)
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


def _load_image(path: Path) -> QtGui.QImage:
    image = QtGui.QImage(str(path))
    if image.isNull():
        raise RuntimeError(f"failed to load image: {path}")
    return image
