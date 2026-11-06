# -*- mode: python -*-
# Builds MCEdit 2 as a folder with mcedit2.exe in it. From the top of the repository:
#
#     .venv/Scripts/python.exe -m PyInstaller mcedit2.spec --noconfirm
#
# Build the Cython modules and compile the .ui files first (see DEVELOPMENT.md). The result is
# dist/MCEdit2/. Its user files, such as settings, block packs and Minecraft jars, live in
# dist/MCEdit2/MCEdit 2 Files/, next to the program.

import os
import shutil
import subprocess
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(SPECPATH)
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

# --- Version ---

with open(os.path.join(ROOT, "version.txt")) as f:
    version = f.read().strip()
try:
    commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT).decode().strip()
    version += "+" + commit
except Exception:
    pass
with open(os.path.join(SRC, "mcedit2", "_version.py"), "w") as f:
    f.write("__version__ = %r\n" % version)

# --- Check the parts that are built before this ---

uiDir = os.path.join(SRC, "mcedit2", "ui")
for root, dirs, files in os.walk(uiDir):
    for name in files:
        if name.endswith(".ui") and not os.path.exists(os.path.join(root, name[:-3] + ".py")):
            raise SystemExit("%s is not compiled. Run: python -m mcedit2.util.gen_ui" % name)

# Compile the translations, so the .qm files match the .ts files.
subprocess.check_call([sys.executable, os.path.join(ROOT, "scripts", "update_translations.py"), "--release-only"])

# --- What goes in ---

# Cython modules import other modules where PyInstaller cannot see it, and plugins are loaded
# from files at run time, so take every module of both packages.
def notTest(name):
    return ".test" not in name and not name.endswith("_test")

hiddenimports = (collect_submodules("mcedit2", filter=notTest)
                 + collect_submodules("mceditlib", filter=notTest))

dataExcludes = ["**/*.c", "**/*.cpp", "**/*.pyx", "**/*.pxd", "**/*.html", "**/__pycache__/**",
                "assets_raw/**", "**/Makefile", "**/*.ts"]
datas = (collect_data_files("mcedit2", excludes=dataExcludes)
         + collect_data_files("mceditlib", excludes=dataExcludes))

a = Analysis([os.path.join(SRC, "mcedit2", "main.py")],
             pathex=[SRC],
             hiddenimports=hiddenimports,
             datas=datas,
             excludes=["tkinter", "IPython", "jupyter_client", "matplotlib", "pytest"],
             noarchive=False)

pyz = PYZ(a.pure)

exe = EXE(pyz,
          a.scripts,
          [],
          exclude_binaries=True,
          name="mcedit2",
          console=False,
          upx=False,
          icon=os.path.join(ROOT, "mcediticon.ico"))

coll = COLLECT(exe,
               a.binaries,
               a.datas,
               upx=False,
               name="MCEdit2")

# --- User files folder next to the program ---

dist = os.path.join(DISTPATH, "MCEdit2")
userFiles = os.path.join(dist, "MCEdit 2 Files")
os.makedirs(os.path.join(userFiles, "blockpacks"), exist_ok=True)
plugins = os.path.join(userFiles, "plugins")
if not os.path.exists(plugins):
    shutil.copytree(os.path.join(SRC, "plugins"), plugins,
                    ignore=shutil.ignore_patterns("__pycache__"))

# Bundle the Minecraft client jars that are in the source tree's version cache, so the program
# starts without asking to download them. The jars belong to Mojang and may not be distributed:
# a build that has them is for your own use only. Set MCEDIT_PUBLIC_BUILD=1 for a build to publish,
# which leaves them out.
BUNDLED_VERSIONS = [] if os.environ.get("MCEDIT_PUBLIC_BUILD") else ["1.21.1"]
versionCache = os.path.join(userFiles, "versionCache")
for v in BUNDLED_VERSIONS:
    jar = os.path.join(ROOT, "MCEdit 2 Files", "versionCache", v + ".jar")
    if os.path.exists(jar):
        os.makedirs(versionCache, exist_ok=True)
        shutil.copy2(jar, versionCache)
        print("Bundled Minecraft %s" % v)
    else:
        print("Minecraft %s is not in MCEdit 2 Files/versionCache, so it is not bundled" % v)
