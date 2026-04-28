import unittest
from subprocess import CompletedProcess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ui.package_meta import (
    PackageMetadata,
    _extract_icon_from_tree,
    analyze_package_group,
    analyze_package_groups,
    extract_archive_icon,
    filter_compatible_package_paths,
)


NATIVE_LAYER_MAGIC = b"<<< deepin linglong layer archive >>>"


def _fake_package(path: Path | str) -> PackageMetadata:
    target = Path(path)
    name = target.stem
    if "labelnova" in name:
        pkg_name = "labelnova"
        pkg_version = "1.0.4-1"
    elif "uos-ai-agent" in name:
        pkg_name = "uos-ai-agent"
        pkg_version = "1.1.56"
    else:
        pkg_name = "demo"
        pkg_version = "1.0.0"
    arch = "amd64"
    if "arm64" in name:
        arch = "arm64"
    elif "loong64" in name:
        arch = "loong64"
    return PackageMetadata(
        path=target,
        package_family="deb",
        package_format="deb",
        pkg_name=pkg_name,
        pkg_version=pkg_version,
        pkg_arch=arch,
        pkg_size=1024,
        sha256="abc123",
        display_name=pkg_name,
        short_description="short",
        full_description="full",
        homepage="https://example.com",
    )


class PackageMetaTests(unittest.TestCase):
    def test_analyze_package_groups_partitions_different_apps(self) -> None:
        with patch("ui.package_meta.analyze_package", side_effect=_fake_package):
            groups = analyze_package_groups(
                (
                    Path("/tmp/labelnova_1.0.4-1_arm64.deb"),
                    Path("/tmp/uos-ai-agent_1.1.56_amd64.deb"),
                    Path("/tmp/labelnova_1.0.4-1_amd64.deb"),
                )
            )

        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].pkg_name, "labelnova")
        self.assertEqual(groups[0].pkg_arches, ("amd64", "arm64"))
        self.assertEqual(groups[1].pkg_name, "uos-ai-agent")
        self.assertEqual(groups[1].pkg_arches, ("amd64",))

    def test_analyze_package_group_reports_mixed_names_with_filenames(self) -> None:
        with patch("ui.package_meta.analyze_package", side_effect=_fake_package):
            with self.assertRaisesRegex(ValueError, "mixed package names are not supported"):
                analyze_package_group(
                    (
                        Path("/tmp/labelnova_1.0.4-1_amd64.deb"),
                        Path("/tmp/uos-ai-agent_1.1.56_amd64.deb"),
                    )
                )

        with patch("ui.package_meta.analyze_package", side_effect=_fake_package):
            try:
                analyze_package_group(
                    (
                        Path("/tmp/labelnova_1.0.4-1_amd64.deb"),
                        Path("/tmp/uos-ai-agent_1.1.56_amd64.deb"),
                    )
                )
            except ValueError as exc:
                message = str(exc)
            else:
                self.fail("expected ValueError")
        self.assertIn("labelnova_1.0.4-1_amd64.deb", message)
        self.assertIn("uos-ai-agent_1.1.56_amd64.deb", message)

    def test_filter_compatible_package_paths_skips_mismatched_packages(self) -> None:
        with patch("ui.package_meta.analyze_package", side_effect=_fake_package):
            accepted, skipped = filter_compatible_package_paths(
                existing_paths=(Path("/tmp/labelnova_1.0.4-1_amd64.deb"),),
                incoming_paths=(
                    Path("/tmp/labelnova_1.0.4-1_arm64.deb"),
                    Path("/tmp/uos-ai-agent_1.1.56_amd64.deb"),
                ),
            )

        self.assertEqual(accepted, (Path("/tmp/labelnova_1.0.4-1_arm64.deb"),))
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0][0], Path("/tmp/uos-ai-agent_1.1.56_amd64.deb"))
        self.assertIn("mixed package names are not supported", skipped[0][1])

    def test_extract_archive_icon_reads_native_linglong_erofs_icon(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package_path = root / "org.demo.layer"
            payload = b'{"info":{"kind":"app","id":"org.demo","version":"1.0.0","arch":["x86_64"]}}'
            package_path.write_bytes(
                NATIVE_LAYER_MAGIC
                + b"\0\0\0"
                + len(payload).to_bytes(4, "little")
                + payload
                + b"erofs"
            )

            def fake_run(args, **kwargs):
                path_arg = next(value for value in args if value.startswith("--path="))
                if "--ls" in args:
                    stdout = "       10    1  org.demo.desktop\n" if path_arg == "--path=/files/share/applications" else ""
                    return CompletedProcess(args, 0, stdout=stdout)
                if path_arg == "--path=/files/share/applications/org.demo.desktop":
                    return CompletedProcess(args, 0, stdout=b"[Desktop Entry]\nIcon=org.demo\n")
                if path_arg == "--path=/files/share/icons/hicolor/512x512/apps/org.demo.png":
                    return CompletedProcess(args, 0, stdout=b"png-bytes")
                return CompletedProcess(args, 0, stdout=b"")

            with (
                patch("ui.package_meta.shutil.which", return_value="/usr/bin/dump.erofs"),
                patch("ui.package_meta.subprocess.run", side_effect=fake_run),
            ):
                icon_path = extract_archive_icon(
                    package_path,
                    pkg_name="org.demo",
                    output_dir=root / "out",
                )

            self.assertIsNotNone(icon_path)
            self.assertEqual(icon_path.read_bytes(), b"png-bytes")

    def test_extract_archive_icon_prefers_native_linglong_desktop_icon_path(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package_path = root / "org.demo.layer"
            payload = b'{"info":{"kind":"app","id":"org.demo","version":"1.0.0","arch":["x86_64"]}}'
            package_path.write_bytes(
                NATIVE_LAYER_MAGIC
                + b"\0\0\0"
                + len(payload).to_bytes(4, "little")
                + payload
                + b"erofs"
            )

            def fake_run(args, **kwargs):
                path_arg = next(value for value in args if value.startswith("--path="))
                if "--ls" in args:
                    stdout = "       10    1  org.demo.desktop\n" if path_arg == "--path=/files/share/applications" else ""
                    return CompletedProcess(args, 0, stdout=stdout)
                if path_arg == "--path=/files/share/applications/org.demo.desktop":
                    return CompletedProcess(args, 0, stdout=b"[Desktop Entry]\nIcon=/files/share/custom/org.demo.png\n")
                if path_arg == "--path=/files/share/custom/org.demo.png":
                    return CompletedProcess(args, 0, stdout=b"direct-png")
                return CompletedProcess(args, 0, stdout=b"")

            with (
                patch("ui.package_meta.shutil.which", return_value="/usr/bin/dump.erofs"),
                patch("ui.package_meta.subprocess.run", side_effect=fake_run),
            ):
                icon_path = extract_archive_icon(
                    package_path,
                    pkg_name="org.demo",
                    output_dir=root / "out",
                )

            self.assertIsNotNone(icon_path)
            self.assertEqual(icon_path.read_bytes(), b"direct-png")

    def test_extract_icon_from_tree_follows_desktop_icon_name_and_supports_svg(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            app_dir = root / "usr/share/applications"
            icon_dir = root / "usr/share/icons/hicolor/scalable/apps"
            asset_dir = root / "usr/share/labelnova/frontend/icons/GHS图标"
            app_dir.mkdir(parents=True)
            icon_dir.mkdir(parents=True)
            asset_dir.mkdir(parents=True)
            (app_dir / "labelnova.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=LabelNova\nIcon=labelnova\n",
                encoding="utf-8",
            )
            expected_icon = icon_dir / "labelnova.svg"
            expected_icon.write_text("<svg/>", encoding="utf-8")
            (asset_dir / "ghs027.png").write_bytes(b"x" * 10000)

            icon_path = _extract_icon_from_tree(root, pkg_name="labelnova", output_dir=root / "out")

            self.assertIsNotNone(icon_path)
            self.assertEqual(icon_path.name, "extracted-icon.svg")
            self.assertEqual(icon_path.read_text(encoding="utf-8"), "<svg/>")

    def test_extract_icon_from_tree_uses_deepin_entries_layout(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            entries = root / "opt/apps/cn.labelnova.app/entries"
            (entries / "applications").mkdir(parents=True)
            (entries / "icons/hicolor/128x128/apps").mkdir(parents=True)
            (root / "opt/apps/cn.labelnova.app/info").write_text(
                '{"appid":"cn.labelnova.app","name":"LabelNova"}',
                encoding="utf-8",
            )
            (entries / "applications/cn.labelnova.app.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=LabelNova\nIcon=cn.labelnova.app\n",
                encoding="utf-8",
            )
            expected_icon = entries / "icons/hicolor/128x128/apps/cn.labelnova.app.png"
            expected_icon.write_bytes(b"deepin-icon")

            icon_path = _extract_icon_from_tree(root, pkg_name="labelnova", output_dir=root / "out")

            self.assertIsNotNone(icon_path)
            self.assertEqual(icon_path.read_bytes(), b"deepin-icon")

    def test_extract_icon_from_tree_prefers_desktop_absolute_icon_path(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            applications = root / "opt/apps/org.demo/entries/applications"
            direct_icon = root / "opt/apps/org.demo/files/share/custom/org.demo.png"
            fallback_icon = root / "opt/apps/org.demo/entries/icons/hicolor/128x128/apps/org.demo.png"
            applications.mkdir(parents=True)
            direct_icon.parent.mkdir(parents=True)
            fallback_icon.parent.mkdir(parents=True)
            (applications / "org.demo.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=Demo\nIcon=/opt/apps/org.demo/files/share/custom/org.demo.png\n",
                encoding="utf-8",
            )
            direct_icon.write_bytes(b"direct-icon")
            fallback_icon.write_bytes(b"fallback-icon")

            icon_path = _extract_icon_from_tree(root, pkg_name="org.demo", output_dir=root / "out")

            self.assertIsNotNone(icon_path)
            self.assertEqual(icon_path.read_bytes(), b"direct-icon")


if __name__ == "__main__":
    unittest.main()
