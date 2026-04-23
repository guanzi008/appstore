import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from appstore.deb import DebParseError, read_deb_package_info
from appstore.models import DebPackageInfo, PackageInfo


class DebPackageInfoTests(unittest.TestCase):
    def test_read_deb_package_info_reads_metadata_and_computes_hash(self) -> None:
        with TemporaryDirectory() as tmpdir:
            deb_path = Path(tmpdir) / "demo_1.2.3_amd64.deb"
            deb_bytes = b"deb package payload"
            deb_path.write_bytes(deb_bytes)

            completed = Mock()
            completed.stdout = "Package: demo\nVersion: 1.2.3\nArchitecture: amd64\n"

            with patch("appstore.deb.subprocess.run", return_value=completed) as run_mock, patch(
                "appstore.deb.Path.read_bytes", side_effect=AssertionError("read_bytes should not be used")
            ):
                package_info = read_deb_package_info(deb_path)

            run_mock.assert_called_once_with(
                ["dpkg-deb", "-f", str(deb_path), "Package", "Version", "Architecture"],
                capture_output=True,
                check=True,
                text=True,
            )
            self.assertEqual(package_info.pkg_name, "demo")
            self.assertEqual(package_info.pkg_version, "1.2.3")
            self.assertEqual(package_info.pkg_arch, "amd64")
            self.assertEqual(package_info.pkg_size, len(deb_bytes))
            self.assertEqual(package_info.sha256, "a19daa93cc145cc397a35074bc9209cca37ec42787accf26068d4e3ca18c09c1")
            self.assertEqual(package_info.file_path, deb_path)
            self.assertEqual(package_info.package_family, "deb")
            self.assertEqual(package_info.package_format, "deb")
            self.assertIsInstance(package_info, PackageInfo)

    def test_deb_package_info_alias_accepts_positional_arguments(self) -> None:
        package_info = DebPackageInfo("demo", "1.0", "amd64", 1, "hash", Path("/tmp/x.deb"))

        self.assertEqual(package_info.pkg_name, "demo")
        self.assertEqual(package_info.pkg_version, "1.0")
        self.assertEqual(package_info.pkg_arch, "amd64")
        self.assertEqual(package_info.pkg_size, 1)
        self.assertEqual(package_info.sha256, "hash")
        self.assertEqual(package_info.file_path, Path("/tmp/x.deb"))
        self.assertEqual(package_info.deb_path, Path("/tmp/x.deb"))

    def test_read_deb_package_info_rejects_malformed_metadata(self) -> None:
        with TemporaryDirectory() as tmpdir:
            deb_path = Path(tmpdir) / "broken-metadata.deb"
            deb_path.write_bytes(b"broken")

            completed = Mock()
            completed.stdout = "Package: demo\nVersion: 1.2.3\n"

            with patch("appstore.deb.subprocess.run", return_value=completed):
                with self.assertRaises(DebParseError):
                    read_deb_package_info(deb_path)

    def test_read_deb_package_info_wraps_subprocess_errors(self) -> None:
        with TemporaryDirectory() as tmpdir:
            deb_path = Path(tmpdir) / "broken.deb"
            deb_path.write_bytes(b"broken")

            error = FileNotFoundError("dpkg-deb")

            with patch("appstore.deb.subprocess.run", side_effect=error):
                with self.assertRaises(DebParseError):
                    read_deb_package_info(deb_path)

    def test_read_deb_package_info_wraps_called_process_error(self) -> None:
        with TemporaryDirectory() as tmpdir:
            deb_path = Path(tmpdir) / "broken-command.deb"
            deb_path.write_bytes(b"broken")

            error = subprocess.CalledProcessError(1, ["dpkg-deb"])

            with patch("appstore.deb.subprocess.run", side_effect=error):
                with self.assertRaises(DebParseError):
                    read_deb_package_info(deb_path)
