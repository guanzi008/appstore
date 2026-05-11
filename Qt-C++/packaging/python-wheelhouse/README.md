This directory is the offline Python dependency wheelhouse used by OBS builds.

`debian/rules` points CMake at this directory so the embedded Python runtime is
assembled without network access. Regenerate it from the repository root with:

```sh
rm -rf Qt-C++/packaging/python-wheelhouse
mkdir -p Qt-C++/packaging/python-wheelhouse
python3 -m pip download --dest Qt-C++/packaging/python-wheelhouse -r appstore/requirements.txt
rm -f Qt-C++/packaging/python-wheelhouse/charset_normalizer-*-manylinux*.whl
python3 -m pip download --dest Qt-C++/packaging/python-wheelhouse \
  --no-deps --only-binary=:all: --platform any --implementation py \
  --abi none --python-version 3.12 'charset-normalizer==3.4.7'
```

Keep `charset_normalizer` as a `py3-none-any` wheel so the same source tree can
build on non-x86 OBS workers. `websockets` is kept as an sdist and is built
natively for the worker architecture during package build.
