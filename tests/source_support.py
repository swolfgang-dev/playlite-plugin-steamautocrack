"""Load this checkout rather than a previously installed plugin version."""
import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
if 'autocrack_source' not in sys.modules:
    spec = importlib.util.spec_from_file_location('autocrack_source', ROOT / '__init__.py',
                                                 submodule_search_locations=[str(ROOT)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)

if 'playlite_plugins' not in sys.modules:
    parent = types.ModuleType('playlite_plugins')
    parent.__path__ = []
    sys.modules['playlite_plugins'] = parent

spec = importlib.util.spec_from_file_location('playlite_plugins.cracktools', ROOT.parent / 'playlite-plugin-crack-tools/__init__.py', submodule_search_locations=[str(ROOT.parent / 'playlite-plugin-crack-tools')])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

sys.modules['playlite_plugins'].cracktools = module
