#!/usr/bin/env python3
from __future__ import annotations

import argparse
import compileall
import os
import shlex
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path


RUNTIME_EXCLUDES = {
    "__pycache__",
    "__phello__",
    "test",
    "tests",
    "idlelib",
    "tkinter",
    "turtledemo",
    "ensurepip",
    "venv",
}

DYNLIB_EXCLUDE_PATTERNS = (
    "_ctypes_test*.so",
    "_test*.so",
    "_tkinter*.so",
    "_xx*.so",
    "xx*.so",
)


def _run(command: list[str], *, env: dict[str, str]) -> None:
    subprocess.run(command, check=True, env=env)


def _copy_tree(source: Path, target: Path) -> None:
    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in RUNTIME_EXCLUDES}

    shutil.copytree(source, target, symlinks=True, ignore=ignore)


def _copy_python_library(output_dir: Path) -> None:
    library_names = [
        value
        for value in (
            sysconfig.get_config_var("LDLIBRARY"),
            sysconfig.get_config_var("INSTSONAME"),
        )
        if value
    ]
    search_dirs = [
        Path(value)
        for value in (
            sysconfig.get_config_var("LIBDIR"),
            sysconfig.get_config_var("LIBPL"),
        )
        if value
    ]
    target_lib_dir = output_dir / "lib"
    target_lib_dir.mkdir(parents=True, exist_ok=True)

    copied: set[Path] = set()
    for library_name in library_names:
        for search_dir in search_dirs:
            candidate = search_dir / library_name
            if not candidate.exists():
                continue
            real_candidate = candidate.resolve()
            target = target_lib_dir / real_candidate.name
            if target not in copied:
                shutil.copy2(real_candidate, target)
                copied.add(target)
            link_target = target_lib_dir / library_name
            if link_target != target and not link_target.exists():
                link_target.symlink_to(target.name)
            break


def _prune_stdlib_runtime(stdlib_target: Path) -> None:
    for child_name in ("__hello__.py", "antigravity.py", "this.py"):
        (stdlib_target / child_name).unlink(missing_ok=True)
    for config_dir in stdlib_target.glob("config-*"):
        if config_dir.is_dir():
            shutil.rmtree(config_dir)

    dynload_dir = stdlib_target / "lib-dynload"
    if dynload_dir.exists():
        for pattern in DYNLIB_EXCLUDE_PATTERNS:
            for path in dynload_dir.glob(pattern):
                path.unlink(missing_ok=True)


def _install_requirements(site_packages: Path, requirements: list[Path], wheelhouse: Path | None) -> None:
    if not requirements:
        return
    site_packages.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    env.setdefault("PIP_NO_CACHE_DIR", "1")

    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-cache-dir",
        "--disable-pip-version-check",
        "--ignore-installed",
        "--target",
        str(site_packages),
    ]
    if wheelhouse is not None:
        if not wheelhouse.is_dir():
            raise FileNotFoundError(f"Python wheelhouse not found: {wheelhouse}")
        command.extend(["--no-index", "--find-links", str(wheelhouse), "--no-build-isolation"])
    extra_args = os.environ.get("APPSTORE_PIP_EXTRA_ARGS", "").strip()
    if extra_args:
        command.extend(shlex.split(extra_args))
    for requirements_file in requirements:
        command.extend(["-r", str(requirements_file)])
    _run(command, env=env)


def _prune_dependency_metadata(site_packages: Path) -> None:
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


def build_runtime(
    output_dir: Path,
    *,
    install_deps: bool,
    requirements: list[Path],
    wheelhouse: Path | None,
) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    stdlib_source = Path(sysconfig.get_path("stdlib")).resolve()
    stdlib_target = output_dir / "lib" / python_version
    _copy_tree(stdlib_source, stdlib_target)
    _prune_stdlib_runtime(stdlib_target)
    _copy_python_library(output_dir)

    site_packages = stdlib_target / "site-packages"
    if install_deps:
        _install_requirements(site_packages, requirements, wheelhouse)
        _prune_dependency_metadata(site_packages)
        compileall.compile_dir(site_packages, quiet=1, force=True)

    compileall.compile_dir(stdlib_target, quiet=1, force=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build the embedded runtime for 应用投递助手.")
    parser.add_argument("runtime_dir")
    parser.add_argument("--install-deps", action="store_true")
    parser.add_argument("--requirements", action="append", default=[])
    parser.add_argument(
        "--wheelhouse",
        type=Path,
        help="Install Python requirements from this offline wheelhouse instead of PyPI.",
    )
    args = parser.parse_args(argv[1:])

    requirements = [Path(value).resolve() for value in args.requirements]
    for requirements_file in requirements:
        if not requirements_file.exists():
            parser.error(f"requirements file not found: {requirements_file}")

    build_runtime(
        Path(args.runtime_dir).resolve(),
        install_deps=args.install_deps,
        requirements=requirements,
        wheelhouse=args.wheelhouse.resolve() if args.wheelhouse else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
