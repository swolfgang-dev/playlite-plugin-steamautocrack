"""Build a standalone plugin release without the base application."""
import hashlib
import argparse
from pathlib import Path
import subprocess
from zipfile import ZipFile, ZIP_DEFLATED
root = Path(__file__).resolve().parent.parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,default=root/'dist')
out=parser.parse_args().output
out.mkdir(parents=True,exist_ok=True)
archive = out / 'plugin.zip'
with ZipFile(archive, 'w', ZIP_DEFLATED) as bundle:
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
    tracked.extend(('container_backend.py','guest/container_entry.py','guest/Dockerfile'))
    for name in sorted(set(filter(None, tracked))):
        relative = Path(name)
        path = root / relative
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve()) and not any(part in ('.git', '.github', 'dist', '__pycache__', 'tests', 'tools', '.venv') for part in relative.parts):
            bundle.write(path, str(relative))
with ZipFile(archive) as bundle:
    for name in ('container_backend.py','guest/container_entry.py','guest/Dockerfile','guest/install_cli.py','guest/vanilla.py','guest/worker.py'):
        assert name in bundle.namelist(),'Missing CLI runtime file: '+name
(out / 'SHA256SUMS').write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '  plugin.zip\n')
print(archive)
