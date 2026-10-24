"""Foreground container entry point; credentials enter only through stdin."""
import json
import os
from pathlib import Path
import shutil
import sys

os.umask(0o077)
prefix=Path(os.environ['WINEPREFIX'])
shutil.copytree('/home/ubuntu/.local/share/playlite/wine-steam-auto-crack',prefix,symlinks=True)
Path.home().mkdir(parents=True,exist_ok=True)
import worker
worker.JOBS.mkdir(parents=True,exist_ok=True)
folder=worker.JOBS/worker.identity(os.environ['PLAYLITE_AUTOCRACK_JOB'])
folder.mkdir(mode=0o700)
data=sys.stdin.buffer.read(16385)
if len(data)>16384:raise ValueError('Processing request exceeds limit.')
request=json.loads(data)
worker.write_json(folder/'request.json',request)
worker.work(folder.name)
result=json.loads((folder/'result.json').read_text())
sys.exit(0 if result.get('Success') else 1)
