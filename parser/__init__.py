"""API de paquete para el front-end SQL."""

import os
import sys

# Los modulos historicos del parser importan token_sql/ast_sql directamente.
_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
if _PACKAGE_DIR not in sys.path:
    sys.path.insert(0, _PACKAGE_DIR)

from .parser import Parser

__all__ = ["Parser"]
