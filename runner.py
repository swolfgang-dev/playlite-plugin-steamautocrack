"""Private subprocess entry point for the native plugin."""
import json
import os
from pathlib import Path
import re
import sys
from playlite.storage import atomic_json
from .engine import process

PRIVATE = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'playlite/steamautocrack'


def write_json(path, value):
    atomic_json(path, value)
    path.chmod(0o600)


def run(job):
    request = json.loads((job / 'request.json').read_text())
    # Credentials only live in memory during the request.
    public = dict(request)
    public.pop('ApiKey', None)
    write_json(job / 'request.json', public)
    def status(message):
        (job / 'status.txt').write_text(message)
    process(request, cancel=lambda: (job / 'cancel').exists(), status=status)
    return dict(Success=True, Message='Processing complete. Originals are backed up and can be restored.')


def main():
    os.umask(0o077)
    if len(sys.argv) != 2 or not re.fullmatch(r'[0-9a-f]{32}', sys.argv[1]):
        return 2
    job = PRIVATE / 'jobs' / sys.argv[1]
    if not job.is_dir():
        return 2
    try:
        result = run(job)
    except Exception as error:
        result = dict(Success=False, Message=str(error) if isinstance(error, ValueError) else 'Native processing failed. Original backups were retained when recovery was needed.')
    finally:
        (job / 'request.json').unlink(missing_ok=True)
    write_json(job / 'result.json', result)
    return 0 if result['Success'] else 1


if __name__ == '__main__':
    sys.exit(main())
