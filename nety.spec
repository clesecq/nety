# PyInstaller build of the release binary: `uv run pyinstaller nety.spec`.
# PyInstaller's keyring hook bundles the backends and the entry-point metadata they need.
import sys

EXCLUDES = [
    # Test and build tools, pulled in through keyring.testing and pkg_resources.
    "pytest", "_pytest", "setuptools", "pkg_resources",
    # Pillow codecs and extensions nety does not use: it only draws lines and saves a PNG.
    "PIL._avif", "PIL._webp", "PIL._imagingft", "PIL._imagingcms", "PIL._imagingmath",
    "PIL._imagingmorph", "PIL.ImageQt",
    # Syntax highlighting and Markdown for rich and prompt_toolkit, loaded only on use.
    "pygments", "markdown_it", "mdurl",
    "pydoc_data",
]

a = Analysis(["src/nety/__main__.py"], excludes=EXCLUDES)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="nety",
    # Drop debug symbols (no-op on Windows).
    strip=sys.platform != "win32",
    upx=False,
    console=True,
)
