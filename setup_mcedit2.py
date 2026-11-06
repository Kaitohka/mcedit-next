"""
    setup-mcedit2

Build the Cython modules in place and compile the .ui files with:

    python setup_mcedit2.py build_ext --inplace
"""
from os import path

import sys
from setuptools import setup, find_packages
from Cython.Build import cythonize
import numpy

with open("version.txt") as f:
    version = f.read().strip()

install_requires = [
    "numpy",
]

include_dirs = [numpy.get_include()]

mcedit2_ext_modules = cythonize(
    [
        "src/mcedit2/rendering/blockmodels.pyx",
        "src/mcedit2/rendering/modelmesh.pyx",
    ],
    include_path=["src"],
)

for m in mcedit2_ext_modules:
    m.include_dirs = include_dirs

sys.path.append(path.join(path.dirname(path.abspath(__file__)), "src"))
from mcedit2.util.gen_ui import compile_ui
compile_ui()

setup(name='mcedit2',
      version=version,
      description="Interactive 3D World Editor for Minecraft Levels",
      # Get strings from http://pypi.python.org/pypi?%3Aaction=list_classifiers
      classifiers=[
          "Development Status :: 2 - Pre-Alpha",
          "Environment :: Win32 (MS Windows)",
          "Environment :: X11 Applications :: Qt",
          "Intended Audience :: End Users/Desktop",
          "Natural Language :: English",
          "Programming Language :: Python :: 3.12",
          "Topic :: Utilities",
          "License :: OSI Approved :: BSD License",
      ],
      keywords='minecraft',
      author='David Vierra',
      author_email='codewarrior0@gmail.com',
      url='https://github.com/mcedit/mcedit2',
      license='BSD-3-Clause',
      packages=find_packages('src', include=["mcedit2*",]),
      package_dir={'': 'src'},
      ext_modules=mcedit2_ext_modules,
      include_dirs=include_dirs,
      include_package_data=True,
      zip_safe=False,
      install_requires=install_requires,
      entry_points="""
      # -*- Entry points: -*-
      [console_scripts]
      mcedit2=mcedit2.main:main
      """,
      )
