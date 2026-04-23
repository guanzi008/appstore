import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from appstore.inspectors import inspect_package
from appstore.linglong import LinglongParseError, read_layer_package_info, read_uab_package_info
from appstore.models import PackageInfo, PackageRecord


def _write_linglong_archive(path: Path, layers: list[dict], extra_members: dict[str, str] | None = None) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("info.json", json.dumps({"layers": layers}))
        for member_name, content in (extra_members or {}).items():
            archive.writestr(member_name, content)


class LinglongPackageInfoTests(unittest.TestCase):
    def test_read_uab_package_info_reads_nested_app_layer_metadata(self) -> None:
        with TemporaryDirectory() as tmpdir:
            package_path = Path(tmpdir) / "demo.uab"
            _write_linglong_archive(
                package_path,
                [
                    {
                        "info": {
                            "kind": "runtime",
                            "id": "org.deepin.runtime",
                            "version": "1.0.0",
                            "arch": ["x86_64"],
                        }
                    },
                    {
                        "info": {
                            "kind": "app",
                            "id": "org.deepin.demo",
                            "version": "0.0.0.1",
                            "arch": ["x86_64"],
                            "module": "runtime",
                        }
                    },
                ],
                extra_members={"files/share/locale/zh_CN/info.json": "{\"ignored\": true}"},
            )

            package_info = read_uab_package_info(package_path)

            self.assertIsInstance(package_info, PackageInfo)
            self.assertEqual(package_info.pkg_name, "org.deepin.demo")
            self.assertEqual(package_info.pkg_version, "0.0.0.1")
            self.assertEqual(package_info.pkg_arch, "x86_64")
            self.assertEqual(package_info.file_path, package_path)
            self.assertEqual(package_info.package_family, "linglong")
            self.assertEqual(package_info.package_format, "uab")

    def test_read_layer_package_info_reads_nested_app_layer_metadata(self) -> None:
        with TemporaryDirectory() as tmpdir:
            package_path = Path(tmpdir) / "demo.layer"
            _write_linglong_archive(
                package_path,
                [
                    {
                        "info": {
                            "kind": "app",
                            "id": "org.deepin.demo",
                            "version": "0.0.0.1",
                            "arch": ["x86_64"],
                            "module": "runtime",
                        }
                    }
                ],
            )

            package_info = read_layer_package_info(package_path)

            self.assertEqual(package_info.pkg_name, "org.deepin.demo")
            self.assertEqual(package_info.package_family, "linglong")
            self.assertEqual(package_info.package_format, "layer")

    def test_read_linglong_package_info_rejects_malformed_metadata(self) -> None:
        with TemporaryDirectory() as tmpdir:
            package_path = Path(tmpdir) / "broken.uab"
            with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("info.json", "{not json")

            with self.assertRaises(LinglongParseError):
                read_uab_package_info(package_path)

    def test_read_linglong_package_info_rejects_missing_app_layer(self) -> None:
        with TemporaryDirectory() as tmpdir:
            package_path = Path(tmpdir) / "missing-app.layer"
            _write_linglong_archive(
                package_path,
                [
                    {
                        "info": {
                            "kind": "runtime",
                            "id": "org.deepin.runtime",
                            "version": "1.0.0",
                            "arch": ["x86_64"],
                        }
                    }
                ],
            )

            with self.assertRaises(LinglongParseError):
                read_layer_package_info(package_path)

    def test_read_linglong_package_info_rejects_multiple_app_layers(self) -> None:
        with TemporaryDirectory() as tmpdir:
            package_path = Path(tmpdir) / "multiple-app.uab"
            _write_linglong_archive(
                package_path,
                [
                    {
                        "info": {
                            "kind": "app",
                            "id": "org.deepin.demo.one",
                            "version": "0.0.0.1",
                            "arch": ["x86_64"],
                        }
                    },
                    {
                        "info": {
                            "kind": "app",
                            "id": "org.deepin.demo.two",
                            "version": "0.0.0.2",
                            "arch": ["x86_64"],
                        }
                    },
                ],
            )

            with self.assertRaises(LinglongParseError):
                read_uab_package_info(package_path)


class PackageInspectorDispatcherTests(unittest.TestCase):
    def test_dispatches_to_family_and_format_specific_readers(self) -> None:
        sentinel = PackageInfo(
            pkg_name="demo",
            pkg_version="1.0.0",
            pkg_arch="amd64",
            pkg_size=1,
            sha256="hash",
            file_path=Path("/tmp/demo.deb"),
            package_family="deb",
            package_format="deb",
        )
        deb_record = PackageRecord(
            row_id=1,
            app_key="demo",
            release_key="stable",
            package_key="pkg-deb",
            package_family="deb",
            package_format="deb",
            file_path=Path("/tmp/demo.deb"),
        )
        uab_record = PackageRecord(
            row_id=2,
            app_key="demo",
            release_key="stable",
            package_key="pkg-uab",
            package_family="linglong",
            package_format="uab",
            file_path=Path("/tmp/demo.uab"),
        )
        layer_record = PackageRecord(
            row_id=3,
            app_key="demo",
            release_key="stable",
            package_key="pkg-layer",
            package_family="linglong",
            package_format="layer",
            file_path=Path("/tmp/demo.layer"),
        )

        with patch("appstore.inspectors.read_deb_package_info", return_value=sentinel) as deb_mock, patch(
            "appstore.inspectors.read_uab_package_info", return_value=sentinel
        ) as uab_mock, patch("appstore.inspectors.read_layer_package_info", return_value=sentinel) as layer_mock:
            self.assertIs(inspect_package(deb_record), sentinel)
            self.assertIs(inspect_package(uab_record), sentinel)
            self.assertIs(inspect_package(layer_record), sentinel)

        deb_mock.assert_called_once_with(deb_record.file_path)
        uab_mock.assert_called_once_with(uab_record.file_path)
        layer_mock.assert_called_once_with(layer_record.file_path)

    def test_dispatcher_rejects_unsupported_structured_family_or_format(self) -> None:
        package = PackageRecord(
            row_id=1,
            app_key="demo",
            release_key="stable",
            package_key="pkg-rpm",
            package_family="rpm",
            package_format="rpm",
            file_path=Path("/tmp/demo.rpm"),
        )

        with self.assertRaises(ValueError):
            inspect_package(package)


if __name__ == "__main__":
    unittest.main()
