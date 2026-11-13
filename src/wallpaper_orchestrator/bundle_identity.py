"""Build-time identity of the Windows standalone distribution.

Development installs retain False. scripts/build_windows.ps1 temporarily changes
this value to True only while compiling the Nuitka bundle, then restores it.
This makes runtime mode independent of the executable filename and Nuitka's
per-module implementation details.
"""

IS_STANDALONE_BUNDLE = False
