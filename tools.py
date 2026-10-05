"""Install native tools privately; upstream sources remain unchanged."""
import fcntl
import uuid
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from xml.sax.saxutils import escape
from playlite.storage import atomic_json

SOURCE_COMMIT = 'f8b93bc2ae76ce3348cc98fd3af8f2b0521c0967'
TOOLS = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'playlite/steamautocrack/tools'


def get_json(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Playlite-SteamAutoCrack/2.0'})
    with urllib.request.urlopen(request, timeout=30) as stream:
        return json.load(stream)


def download(url, target, check):
    request = urllib.request.Request(url, headers={'User-Agent': 'Playlite-SteamAutoCrack/2.0'})
    with urllib.request.urlopen(request, timeout=30) as incoming, target.open('wb') as outgoing:
        while chunk := incoming.read(1024 * 1024):
            check()
            outgoing.write(chunk)


def run_command(command, check, cwd=None):
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            import time
            while process.poll() is None:
                check()
                time.sleep(0.1)
            if process.returncode:
                output.seek(0)
                tail = output.read().decode(errors='replace')[-2000:]
                raise ValueError('Native tool setup failed:\n' + tail)
        finally:
            if process.poll() is None:
                import signal
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def extract_tar(archive, destination):
    with tarfile.open(archive) as source:
        if hasattr(tarfile, 'data_filter'):
            source.extractall(destination, filter='data')
        else:
            # Older supported Python versions do not have extraction filters.
            for member in source.getmembers():
                path = Path(member.name)
                if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                    raise ValueError('Unsupported or unsafe tool archive entry.')
            source.extractall(destination)


def promote(root, prepared, commit=lambda: None):
    """Replace a set of tools together, retaining old versions until commit succeeds."""
    previous = {}
    promoted = []
    try:
        for name, source in prepared.items():
            target = root / name
            if target.exists():
                old = root / (name + '.previous-' + uuid.uuid4().hex)
                target.rename(old)
                previous[name] = old
            source.rename(target)
            promoted.append(name)
        commit()
    except Exception:
        for name in reversed(promoted):
            shutil.rmtree(root / name)
        for name, old in previous.items():
            old.rename(root / name)
        raise
    for old in previous.values():
        shutil.rmtree(old, ignore_errors=True)


def install(check=lambda: None, status=lambda text: None, root=TOOLS):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.setup.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another native tool installation is already running.') from None
        return _install(check, status, root)


def _install(check, status, root):
    machine = platform.machine().lower()
    rid = {'x86_64': 'linux-x64', 'amd64': 'linux-x64', 'aarch64': 'linux-arm64'}.get(machine)
    if not rid:
        raise ValueError('Native tool setup supports Linux x64 and ARM64 hosts.')
    sevenzip = shutil.which('7z') or shutil.which('7zz')
    if not sevenzip:
        raise ValueError('Install native 7-Zip (7z or 7zz) before setting up emulator tools.')
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='setup-', dir=root) as workspace:
        stage = Path(workspace)
        dotnet = root / 'dotnet' / 'dotnet'
        if not dotnet.is_file():
            status('Downloading the Linux .NET SDK for the native unpacker…')
            meta = get_json('https://dotnetcli.blob.core.windows.net/dotnet/release-metadata/10.0/releases.json')
            sdk = next(file for file in meta['releases'][0]['sdk']['files']
                       if file['rid'] == rid and file['name'].endswith('.tar.gz'))
            archive = stage / 'sdk.tar.gz'
            download(sdk['url'], archive, check)
            with archive.open('rb') as stream:
                checksum = hashlib.sha512()
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    checksum.update(chunk)
                actual = checksum.hexdigest()
            if actual != sdk['hash'].lower():
                raise ValueError('Linux .NET SDK checksum did not match.')
            extracted = stage / 'dotnet'
            extracted.mkdir()
            extract_tar(archive, extracted)
            check()
            promote(root, {'dotnet': extracted})
        status('Downloading original Steamless sources…')
        archive = stage / 'source.zip'
        download(f'https://codeload.github.com/SteamAutoCracks/Steam-auto-crack/zip/{SOURCE_COMMIT}', archive, check)
        source_root = stage / 'source'
        with zipfile.ZipFile(archive) as source:
            for entry in source.infolist():
                if Path(entry.filename).is_absolute() or '..' in Path(entry.filename).parts:
                    raise ValueError('Invalid upstream source archive.')
            source.extractall(source_root)
        upstream = next(source_root.iterdir())
        project_root = stage / 'build'
        project_root.mkdir()
        references = ''.join(f'<Compile Include="{escape(str(path), {chr(34): "&quot;"})}/**/*.cs" Exclude="{escape(str(path), {chr(34): "&quot;"})}/obj/**;{escape(str(path), {chr(34): "&quot;"})}/bin/**" />'
                             for path in upstream.glob('Steamless.*') if path.is_dir())
        program = Path(__file__).parent / 'native' / 'Program.cs'
        project = project_root / 'NativeSteamless.csproj'
        project.write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net10.0</TargetFramework>'
                           '<OutputType>Exe</OutputType><ImplicitUsings>enable</ImplicitUsings><AllowUnsafeBlocks>true</AllowUnsafeBlocks>'
                           '</PropertyGroup><ItemGroup>' + references +
                           f'<Compile Include="{escape(str(program), {chr(34): "&quot;"})}" /><PackageReference Include="Iced" Version="1.21.0" />'
                           '</ItemGroup></Project>')
        status('Building the native Linux SteamStub unpacker…')
        output = stage / 'unpacker'
        run_command([str(dotnet), 'build', str(project), '-c', 'Release', '-o', str(output)], check)
        launcher = output / 'steamless'
        launcher.write_text('#!/bin/sh\nexec "$(dirname "$0")/../dotnet/dotnet" "$(dirname "$0")/NativeSteamless.dll" "$@"\n')
        launcher.chmod(0o755)
        # Keep upstream attribution alongside the locally built tool.
        shutil.copy2(upstream / 'LICENSE.md', output / 'SteamAutoCrack-LICENSE.md')
        (output / 'Steamless-LICENSE.txt').write_text('Steamless: Copyright (c) 2015–2024 atom0s.\n'
            'Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International.\n'
            'https://creativecommons.org/licenses/by-nc-nd/4.0/\n'
            'Original sources: https://github.com/SteamAutoCracks/Steam-auto-crack\n')
        status('Downloading Goldberg emulator libraries…')
        release = get_json('https://api.github.com/repos/Detanup01/gbe_fork/releases/latest')
        emulator = stage / 'emulator'
        emulator.mkdir()
        for prefix, platform_dir in [('emu-win-release-vs22', 'windows'), ('emu-linux-release', 'linux')]:
            asset = next((asset for asset in release['assets'] if asset['name'].startswith(prefix)), None)
            if asset is None:
                raise ValueError('Upstream emulator release has no supported archive.')
            archive = stage / asset['name']
            download(asset['browser_download_url'], archive, check)
            destination = emulator / platform_dir
            destination.mkdir()
            if archive.name.endswith('.tar.bz2'):
                extract_tar(archive, destination)
            else:
                listing = subprocess.run([sevenzip, 'l', '-slt', str(archive)], capture_output=True, text=True, check=True).stdout
                entries = listing.split('----------', 1)[-1]
                for line in entries.splitlines():
                    if line.startswith('Path = '):
                        path = Path(line[7:].replace('\\', '/'))
                        if path.is_absolute() or '..' in path.parts:
                            raise ValueError('Invalid emulator archive path.')
                    if line.startswith(('Symbolic Link = ', 'Hard Link = ')):
                        raise ValueError('Emulator archive contains unsupported links.')
                run_command([sevenzip, 'x', str(archive), '-o' + str(destination), '-y'], check)
        check()
        promote(root, {'unpacker': output, 'emulator': emulator},
                lambda: atomic_json(root / 'versions.json',
                                    dict(SteamlessSource=SOURCE_COMMIT, Emulator=release['tag_name'])))
        status('Native tools installed. No Wine prefix or Windows CLI is required.')
    return dict(EmulatorDirectory=str(root / 'emulator'), UnpackerPath=str(root / 'unpacker/steamless'))
