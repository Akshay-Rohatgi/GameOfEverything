"""Packaging — assemble built entities into a self-contained output package."""

from goe.packaging.packager import package
from goe.packaging.solve_script import UnsupportedActionError, compile_solve_script

__all__ = ["package", "compile_solve_script", "UnsupportedActionError"]
