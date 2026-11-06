#!/usr/bin/env python3
"""Verify the development environment by actually exercising each tool.

Exit code 0 means every check passed, 1 means at least one failed.
"""
import importlib.metadata
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MSVC_HELP = (
    "Install 'Microsoft C++ Build Tools' with the 'Desktop development with C++' workload:\n"
    "    https://visualstudio.microsoft.com/visual-cpp-build-tools/"
)


def check_python():
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError(f"expected Python 3.12, found {sys.version.split()[0]}")
    return sys.version.split()[0]


def check_pins():
    """Every pin in requirements-py3.txt must match what is installed."""
    pins = 0
    for line in (ROOT / "requirements-py3.txt").read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        name, _, wanted = line.partition("==")
        installed = importlib.metadata.version(name)
        if installed != wanted:
            raise RuntimeError(f"{name}: pinned {wanted}, installed {installed}")
        pins += 1
    return f"{pins} pinned packages match"


def check_cython_build():
    """Compile and import a small numpy-using Cython extension, as M1 will need."""
    source = textwrap.dedent(
        """
        # cython: language_level=3
        import numpy
        cimport numpy
        numpy.import_array()
        def total(numpy.ndarray[numpy.uint16_t, ndim=1] values):
            cdef Py_ssize_t i
            cdef unsigned long result = 0
            for i in range(values.shape[0]):
                result += values[i]
            return result
        """
    )
    setup = textwrap.dedent(
        """
        import numpy
        from Cython.Build import cythonize
        from setuptools import setup
        modules = cythonize("probe.pyx", quiet=True)
        for module in modules:
            module.include_dirs = [numpy.get_include()]
        setup(name="probe", ext_modules=modules)
        """
    )
    use = "import numpy, probe; assert probe.total(numpy.array([1, 2, 3], dtype='uint16')) == 6"
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "probe.pyx").write_text(source)
        (Path(tmp) / "setup.py").write_text(setup)
        build = subprocess.run(
            [sys.executable, "setup.py", "build_ext", "--inplace"], cwd=tmp, capture_output=True, text=True
        )
        if build.returncode != 0:
            tail = "\n".join((build.stdout + build.stderr).strip().splitlines()[-4:])
            raise RuntimeError(f"Cython extension failed to build:\n{tail}\n{MSVC_HELP}")
        subprocess.run([sys.executable, "-c", use], cwd=tmp, check=True, capture_output=True)
    return "compiled and ran a numpy extension"


def check_opengl():
    """Create a Qt6 compatibility-profile context and run the legacy GL calls the renderer relies on."""
    from PySide6 import QtCore, QtGui, QtOpenGLWidgets  # noqa: F401  (import proves the module exists)
    from OpenGL import GL

    app = QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])

    surface_format = QtGui.QSurfaceFormat()
    surface_format.setRenderableType(QtGui.QSurfaceFormat.RenderableType.OpenGL)
    surface_format.setProfile(QtGui.QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
    surface_format.setVersion(2, 1)

    context = QtGui.QOpenGLContext()
    context.setFormat(surface_format)
    if not context.create():
        raise RuntimeError("could not create an OpenGL compatibility context")

    surface = QtGui.QOffscreenSurface()
    surface.setFormat(context.format())
    surface.create()
    if not context.makeCurrent(surface):
        raise RuntimeError("could not make the OpenGL context current")

    try:
        GL.glGetError()
        display_list = GL.glGenLists(1)
        GL.glNewList(display_list, GL.GL_COMPILE)
        GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
        GL.glDisableClientState(GL.GL_VERTEX_ARRAY)
        GL.glEndList()
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glPushMatrix()
        GL.glPopMatrix()
        GL.glCallList(display_list)
        GL.glDeleteLists(display_list, 1)
        error = GL.glGetError()
        if error != GL.GL_NO_ERROR:
            raise RuntimeError(f"fixed-function GL calls reported error 0x{error:x}")
        version = GL.glGetString(GL.GL_VERSION).decode()
        renderer = GL.glGetString(GL.GL_RENDERER).decode()
    finally:
        context.doneCurrent()
    del app
    return f"fixed-function calls OK on OpenGL {version} ({renderer})"


def check_lz4():
    import lz4.block

    data = b"minecraft" * 100
    if lz4.block.decompress(lz4.block.compress(data)) != data:
        raise RuntimeError("LZ4 round trip mismatch")
    return "round trip OK"


CHECKS = [
    ("Python", check_python),
    ("Pinned packages", check_pins),
    ("Cython build", check_cython_build),
    ("Qt6 + OpenGL", check_opengl),
    ("LZ4", check_lz4),
]


def main():
    failed = 0
    for name, check in CHECKS:
        try:
            print(f"[ OK ] {name}: {check()}")
        except Exception as error:  # report every failing check rather than stopping at the first
            failed += 1
            print(f"[FAIL] {name}: {error}")
    print("All checks passed." if not failed else f"{failed} check(s) failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
