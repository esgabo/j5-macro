"""The package works without the ``vision`` extra; the toolkit explains how to get it."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

BLOCKER = textwrap.dedent("""
    import importlib.abc, pkgutil, sys

    BLOCKED = {"cv2", "numpy", "mss"}

    class Blocker(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in BLOCKED:
                raise ImportError(f"{name} is blocked")
            return None

    sys.meta_path.insert(0, Blocker())
""")


def run_without_extra(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-c", BLOCKER + textwrap.dedent(code)],
                          capture_output=True, text=True, timeout=120)


def test_package_imports_without_the_vision_extra() -> None:
    result = run_without_extra("""
        import j5_macro
        IMPLEMENTATIONS = {"capture", "templates", "matching", "drawing"}
        failures = []
        for info in pkgutil.walk_packages(j5_macro.__path__, "j5_macro."):
            last = info.name.rsplit(".", 1)[1]
            if last == "__main__" or (info.name.startswith("j5_macro.vision.")
                                      and last in IMPLEMENTATIONS):
                continue
            try:
                __import__(info.name)
            except ImportError as exc:
                if "blocked" in str(exc):
                    failures.append(f"{info.name}: {exc}")
        import j5_macro.vision as vision
        assert vision.Match and vision.create_toolkit
        assert not BLOCKED & set(sys.modules), BLOCKED & set(sys.modules)
        print("\\n".join(failures) or "ok")
    """)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_create_toolkit_without_the_extra_gives_an_install_hint() -> None:
    result = run_without_extra("""
        from j5_macro.vision import create_toolkit
        try:
            create_toolkit()
        except ImportError as exc:
            print(exc)
    """)
    assert result.returncode == 0, result.stderr
    assert 'pip install "j5-macro[vision]"' in result.stdout


@pytest.mark.skipif(sys.platform != "linux", reason="X11 display selection")
def test_create_toolkit_without_a_screen_is_a_runtime_error() -> None:
    pytest.importorskip("cv2")
    pytest.importorskip("mss")
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            from j5_macro.vision import create_toolkit
            try:
                create_toolkit()
            except RuntimeError as exc:
                print("RuntimeError:", exc)
        """)],
        capture_output=True, text=True, timeout=120,
        env={"DISPLAY": ":4242", "PATH": "", "XAUTHORITY": "/nonexistent"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("RuntimeError:")


def test_lazy_names_resolve() -> None:
    pytest.importorskip("cv2")
    pytest.importorskip("mss")
    import j5_macro.vision as vision
    from j5_macro.vision.matching import locate

    assert vision.locate is locate
    with pytest.raises(AttributeError):
        _ = vision.no_such_name
