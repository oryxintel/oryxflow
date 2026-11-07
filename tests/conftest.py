"""Suite-wide test setup.

Tests write task modules to tmp dirs, run them, rewrite them (often at the same size, ``FACTOR = 1`` -> ``2``) and
import them again within the same second. CPython validates cached bytecode by the source's mtime in whole seconds
plus its size, so a cached .pyc from the first import would run the OLD code (tests/test_stale_bytecode.py covers
the engine's guard for that case). The suite never writes bytecode for the modules it creates.
"""
import sys

sys.dont_write_bytecode = True
