import tempfile
import unittest
from pathlib import Path

from appstore.desktop_entry import DesktopEntry, choose_desktop_entry, load_desktop_entry, split_desktop_exec


class DesktopEntryTests(unittest.TestCase):
    def test_split_desktop_exec_removes_field_codes(self) -> None:
        self.assertEqual(
            split_desktop_exec("env BAMF_DESKTOP_FILE_HINT=/usr/share/applications/demo.desktop demo-app %U"),
            ("env", "BAMF_DESKTOP_FILE_HINT=/usr/share/applications/demo.desktop", "demo-app"),
        )

    def test_load_desktop_entry_reads_application_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            desktop_path = Path(temp_dir) / "demo.desktop"
            desktop_path.write_text(
                "\n".join(
                    [
                        "[Desktop Entry]",
                        "Type=Application",
                        "Name=Demo App",
                        "Exec=demo-app --flag %F",
                        "StartupWMClass=DemoApp",
                        "NoDisplay=false",
                    ]
                ),
                encoding="utf-8",
            )

            entry = load_desktop_entry(desktop_path)

        self.assertEqual(entry.name, "Demo App")
        self.assertEqual(entry.exec_command, ("demo-app", "--flag"))
        self.assertEqual(entry.startup_wm_class, "DemoApp")

    def test_choose_desktop_entry_prefers_visible_non_terminal_entry(self) -> None:
        entries = (
            DesktopEntry(Path("/tmp/terminal.desktop"), "Terminal App", ("demo",), terminal=True),
            DesktopEntry(Path("/tmp/gui.desktop"), "GUI App", ("demo-gui",)),
        )
        self.assertEqual(choose_desktop_entry(entries).path.name, "gui.desktop")


if __name__ == "__main__":
    unittest.main()
