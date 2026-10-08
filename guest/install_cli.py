"""Build the upstream Windows CLI inside the Steam VM."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
import urllib.request

VERSION = '3.5.1.0'
RECIPE = 2
COMMIT = 'f8b93bc2ae76ce3348cc98fd3af8f2b0521c0967'
SOURCE_SHA256 = 'ba664a7ba8c21c92e917ef92e0bdd1cf1f3c1ba321d0633e425b909ac0d93665'
SDK_VERSION = '10.0.401'
SDK_SHA512 = '51c8b999af9e8dd9998c9edc5944e19a90788862068acd38694e098889054ce8c23d4f0c5cccfa16bf187d044562359e5ee69a9f8ad0bbe913ba90311fbce25b'
PREFIX = Path.home() / '.local/share/playlite/wine-steam-auto-crack'
CLI = PREFIX / 'drive_c/SteamAutoCrackCLI'


def download(url, target, algorithm, expected, check):
    digest = hashlib.new(algorithm)
    request = urllib.request.Request(url, headers={'User-Agent': 'Playlite-SteamAutoCrack/3'})
    with urllib.request.urlopen(request, timeout=60) as incoming, target.open('wb') as output:
        while chunk := incoming.read(1024 * 1024):
            check()
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != expected:
        raise ValueError('CLI setup download checksum mismatch.')


def install(check=lambda: None, status=lambda text: None):
    marker = CLI / 'playlite-cli-version.json'
    if (CLI / 'SteamAutoCrack.CLI.exe').is_file() and marker.is_file():
        if json.loads(marker.read_text()).get('commit') == COMMIT and json.loads(marker.read_text()).get('recipe') == RECIPE:
            status('Windows CLI ' + VERSION + ' is installed in the Steam VM.')
            return
    gui = PREFIX / 'drive_c/SteamAutoCrack'
    if not (gui / 'SteamAutoCrack.exe').is_file() or not (gui / 'Goldberg').is_dir():
        raise ValueError('Install Wine and Steam Auto Crack in this VM before setting up its CLI.')
    if (gui / 'playlite-release-version.txt').read_text().strip() != VERSION:
        raise ValueError('The VM GUI version does not match this CLI recipe (' + VERSION + ').')
    cache = Path.home() / '.cache/playlite/steamautocrack-cli'
    cache.mkdir(parents=True, exist_ok=True)
    sdk = cache / ('sdk-' + SDK_VERSION)
    if not (sdk / 'dotnet').is_file():
        status('Downloading the verified .NET SDK inside the VM…')
        with tempfile.TemporaryDirectory(dir=cache) as folder:
            archive = Path(folder) / 'sdk.tar.gz'
            download('https://builds.dotnet.microsoft.com/dotnet/Sdk/' + SDK_VERSION +
                     '/dotnet-sdk-' + SDK_VERSION + '-linux-x64.tar.gz', archive,
                     'sha512', SDK_SHA512, check)
            prepared = Path(folder) / 'sdk'
            prepared.mkdir()
            with tarfile.open(archive) as source:
                source.extractall(prepared, filter='data')
            check()
            if sdk.exists():
                shutil.rmtree(sdk)
            prepared.rename(sdk)
    status('Downloading the verified upstream Windows CLI source…')
    with tempfile.TemporaryDirectory(dir=cache) as folder:
        folder = Path(folder)
        archive = folder / 'source.tar.gz'
        download('https://codeload.github.com/SteamAutoCracks/Steam-auto-crack/tar.gz/' + COMMIT,
                 archive, 'sha256', SOURCE_SHA256, check)
        with tarfile.open(archive) as source:
            source.extractall(folder, filter='data')
        source = folder / ('Steam-auto-crack-' + COMMIT)
        # Upstream still passes option descriptions as constructor aliases.
        # System.CommandLine 2.0 rejects their spaces before any command runs.
        program = source / 'SteamAutoCrack.CLI/Program.cs'
        text = program.read_text(encoding='utf-8-sig')
        import re
        text, count = re.subn(r'new Option<(bool|FileInfo\?)>\(\s*"(--debug|--force|--path)",\s*"([^"\n]+)"\s*\)',
                             r'new Option<\1>("\2") { Description = "\3" }', text)
        if count != 3:
            raise ValueError('The pinned CLI option compatibility patch no longer matches upstream.')
        program.write_text(text)
        prepared = folder / 'publish'
        env = os.environ.copy()
        env.update(DOTNET_ROOT=str(sdk), DOTNET_CLI_TELEMETRY_OPTOUT='1',
                   DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1', DOTNET_NOLOGO='1',
                   NUGET_PACKAGES=str(cache / 'nuget'))
        status('Building upstream ' + VERSION + ' for Windows x86 inside the VM…')
        with tempfile.TemporaryFile() as log:
            child = subprocess.Popen([str(sdk / 'dotnet'), 'publish',
                str(source / 'SteamAutoCrack.CLI/SteamAutoCrack.CLI.csproj'),
                '-c', 'Release', '-r', 'win-x86', '--self-contained', 'false',
                '-p:Platform=x86', '-p:PublishSingleFile=true', '-p:EnableWindowsTargeting=true',
                '-o', str(prepared)], env=env, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True)
            try:
                while child.poll() is None:
                    check()
                    time.sleep(.2)
                if child.returncode:
                    log.seek(0)
                    raise ValueError('Windows CLI build failed:\n' + log.read().decode(errors='replace')[-3000:])
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait()
        if not (prepared / 'SteamAutoCrack.CLI.exe').is_file():
            raise ValueError('The upstream build did not produce a Windows CLI.')
        (prepared / 'Goldberg').symlink_to(gui / 'Goldberg', target_is_directory=True)
        shutil.copyfile(source / 'LICENSE.md', prepared / 'UPSTREAM-LICENSE.md')
        (prepared / 'playlite-cli-version.json').write_text(json.dumps(dict(version=VERSION, commit=COMMIT, recipe=RECIPE)))
        check()
        old = CLI.with_name('SteamAutoCrackCLI.previous')
        if old.exists():
            shutil.rmtree(old)
        if CLI.exists():
            CLI.rename(old)
        try:
            shutil.copytree(prepared, CLI, symlinks=True)
        except Exception:
            shutil.rmtree(CLI, ignore_errors=True)
            if old.exists():
                old.rename(CLI)
            raise
        shutil.rmtree(old, ignore_errors=True)
    status('Windows CLI ' + VERSION + ' is ready in the Steam VM.')
