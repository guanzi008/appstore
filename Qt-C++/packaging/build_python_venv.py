#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import venv
from pathlib import Path


def _python_executable(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _run(command: list[str], *, env: dict[str, str]) -> None:
    subprocess.run(command, check=True, env=env)


def _site_packages_dirs(venv_dir: Path) -> list[Path]:
    lib_dir = venv_dir / "lib"
    if not lib_dir.exists():
        return []
    return [path / "site-packages" for path in lib_dir.glob("python*") if (path / "site-packages").exists()]


def _compile_site_packages(python_bin: Path, venv_dir: Path, *, env: dict[str, str]) -> None:
    for site_packages in _site_packages_dirs(venv_dir):
        _run([str(python_bin), "-m", "compileall", "-q", "-f", str(site_packages)], env=env)


def _prune_packaging_tools(venv_dir: Path) -> None:
    bin_dir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
    for pattern in ("pip", "pip3", "pip3.*"):
        for path in bin_dir.glob(pattern):
            path.unlink(missing_ok=True)
    for site_packages in _site_packages_dirs(venv_dir):
        for pattern in (
            "pip",
            "pip-*",
            "setuptools",
            "setuptools-*",
            "wheel",
            "wheel-*",
            "pkg_resources",
        ):
            for path in site_packages.glob(pattern):
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)


def build_venv(venv_dir: Path, *, install_deps: bool, requirements: list[Path]) -> None:
    builder = venv.EnvBuilder(
        clear=True,
        with_pip=True,
        symlinks=False,
        system_site_packages=True,
    )
    builder.create(venv_dir)

    python_bin = _python_executable(venv_dir)
    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    env.setdefault("PIP_NO_CACHE_DIR", "1")

    if not install_deps:
        return

    pip_command = [
        str(python_bin),
        "-m",
        "pip",
        "install",
        "--no-cache-dir",
        "--disable-pip-version-check",
        "--ignore-installed",
    ]
    extra_args = os.environ.get("APPSTORE_PIP_EXTRA_ARGS", "").strip()
    if extra_args:
        pip_command.extend(shlex.split(extra_args))
    for requirements_file in requirements:
        pip_command.extend(["-r", str(requirements_file)])
    if requirements:
        _run(pip_command, env=env)
    _compile_site_packages(python_bin, venv_dir, env=env)
    _prune_packaging_tools(venv_dir)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build the bundled UTPublisher Python venv.")
    parser.add_argument("venv_dir")
    parser.add_argument("--install-deps", action="store_true")
    parser.add_argument("--requirements", action="append", default=[])
    args = parser.parse_args(argv[1:])

    requirements = [Path(value).resolve() for value in args.requirements]
    for requirements_file in requirements:
        if not requirements_file.exists():
            parser.error(f"requirements file not found: {requirements_file}")

    build_venv(Path(args.venv_dir).resolve(), install_deps=args.install_deps, requirements=requirements)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
