"""
    setup_mceditlib

Build the Cython modules in place with:

    python setup_mceditlib.py build_ext --inplace
"""

from setuptools import setup, find_packages
from Cython.Build import cythonize

import numpy

with open("version.txt") as f:
    version = f.read().strip()

install_requires = [
    "numpy",
]

include_dirs = [numpy.get_include()]

mceditlib_ext_modules = cythonize([
    "src/mceditlib/nbt.pyx",
    "src/mceditlib/relight/with_cython.pyx",
])

for m in mceditlib_ext_modules:
    m.include_dirs = include_dirs

setup(name='mceditlib',
      version=version,
      description="Python library for editing Minecraft levels",
      # Get strings from http://pypi.python.org/pypi?%3Aaction=list_classifiers
      classifiers=[
          "Development Status :: 4 - Beta",
          "Environment :: Console",
          "Intended Audience :: Developers",
          "Natural Language :: English",
          "Operating System :: OS Independent",
          "Programming Language :: Python :: 3.12",
          "Topic :: Utilities",
          "License :: OSI Approved :: BSD License",
      ],
      keywords='minecraft',
      author='David Vierra',
      author_email='codewarrior0@gmail.com',
      url='https://github.com/mcedit/mcedit2',
      license='BSD-3-Clause',
      packages=find_packages('src', include=["mceditlib*"]),
      package_data={'mceditlib': ['blocktypes/*.json', 'blocktypes/packs/*/*', 'anvil/biomes.csv']},
      package_dir={'': 'src'},
      ext_modules=mceditlib_ext_modules,
      include_dirs=include_dirs,
      include_package_data=True,
      zip_safe=False,
      install_requires=install_requires,
      )
