"""Load this checkout rather than a previously installed plugin version."""
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if 'autocrack_source' not in sys.modules:
    spec = importlib.util.spec_from_file_location('autocrack_source', ROOT / '__init__.py',
                                                 submodule_search_locations=[str(ROOT)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
