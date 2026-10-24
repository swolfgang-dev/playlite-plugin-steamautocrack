"""Run the Windows CLI in a private Wine container, without a VM or host Wine."""
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid

IMAGE='playlite-autocrack-cli:3.5.1.0-1'
LABEL='io.playlite.autocrack.recipe'
RECIPE='1'
ASSETS=Path(__file__).parent


def downloader_module(name):
    package='playlite_plugins.steamdepotdownloader'
    try:return importlib.import_module(package+'.'+name)
    except ModuleNotFoundError:
        from playlite.providers import discover_plugins
        if 'SteamDepotDownloader' not in discover_plugins():
            raise ValueError('Install and enable Steam Downloader first.') from None
        return importlib.import_module(package+'.'+name)


def require_cli():
    try:
        result=subprocess.run(['docker','image','inspect',IMAGE],capture_output=True,text=True,timeout=30)
        if result.returncode or json.loads(result.stdout)[0]['Config'].get('Labels',{}).get(LABEL)!=RECIPE:
            raise ValueError('missing')
    except (OSError,subprocess.TimeoutExpired,ValueError,KeyError,IndexError,TypeError):
        raise ValueError('Set up the CLI in SteamAutoCrack settings first.') from None


def setup(root=None,cancel=lambda:False,status=lambda text:None):
    if not shutil.which('docker'):raise ValueError('Docker is required; no host Wine or .NET installation is needed.')
    try:require_cli()
    except ValueError:pass
    else:
        status('The isolated Wine CLI is already installed.')
        return {'Success':True,'Message':'The isolated Wine CLI is ready.'}
    backend=downloader_module('vm_backend')
    # Reuse the pinned, checksum-verified Wine/.NET/application recipe. It runs
    # only in the image build, never on the host or in an existing VM.
    tools=Path(backend.__file__).parent/'tools/vm'
    from playlite_plugins.cracktools import backup
    with tempfile.TemporaryDirectory(prefix='playlite-autocrack-build-') as temporary:
        context=Path(temporary)
        shutil.copytree(ASSETS/'guest',context/'guest',ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(ASSETS/'guest/Dockerfile',context/'Dockerfile')
        shutil.copyfile(tools/'install-steam-auto-crack.sh',context/'install-runtime.sh')
        shutil.copyfile(tools/'prepare-wine-fonts.py',context/'prepare-wine-fonts.py')
        shutil.copyfile(backup.__file__,context/'backup.py')
        status('Building the private Wine CLI container. Tool downloads use the build connection; game-info requests use the VPN.')
        with tempfile.TemporaryFile() as log:
            child=subprocess.Popen(['docker','build','--label',LABEL+'='+RECIPE,'-t',IMAGE,str(context)],stdout=log,stderr=subprocess.STDOUT)
            deadline=time.monotonic()+1800
            try:
                while child.poll() is None:
                    if cancel():raise ValueError('CLI setup cancelled.')
                    if time.monotonic()>deadline:raise ValueError('CLI setup timed out.')
                    time.sleep(.2)
                if child.returncode:
                    log.seek(0,2);log.seek(max(0,log.tell()-12000))
                    raise ValueError('CLI build failed:\n'+log.read().decode(errors='replace'))
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:child.wait(timeout=10)
                    except subprocess.TimeoutExpired:child.kill();child.wait()
    require_cli()
    return {'Success':True,'Message':'The isolated Wine CLI is ready. No VM is required.'}


def game_path(value):
    root=Path(value).expanduser()
    if not root.is_absolute() or root.is_symlink() or not root.is_dir() or any(c in str(root) for c in '\r\n\0,\\'):
        raise ValueError('Choose an installed game folder with a valid Windows path.')
    return root.resolve()


def container_args(root,work,job,network=None):
    if os.getuid()==0:raise ValueError('Do not process games as root.')
    if network is not None:
        args=network.user_probe_args('')
        original=downloader_module('constants').IMAGE
        index=args.index(original)
        args[index:]=[IMAGE,'-a','python3','/app/container_entry.py']
        args[args.index('--entrypoint')+1]='/usr/bin/xvfb-run'
        args[args.index('--memory')+1]='4g'
        args[args.index('--pids-limit')+1]='512'
    else:
        args=['run','--rm','--network','none','--user',f'{os.getuid()}:{os.getgid()}',
              '--cap-drop','ALL','--security-opt','no-new-privileges','--read-only',
              '--pids-limit','512','--memory','4g',IMAGE]
        index=args.index(IMAGE)
    args[1:1]=['--name',job,'-i']
    index=args.index(IMAGE)
    args[index:index]=['--mount',f'type=bind,src={root},dst=/mnt/standalone/game',
                       '--mount',f'type=bind,src={work},dst=/work',
                       '--tmpfs','/tmp:rw,nosuid,nodev,size=256m',
                       '-e','PLAYLITE_AUTOCRACK_JOB='+job.rsplit('-',1)[-1]]
    return args


def process(request,cancel=lambda:False,status=lambda text:None):
    root=game_path(request['InstallDirectory'])
    if cancel():raise ValueError('Cancelled before changing game files.')
    if request.get('Restore'):
        from playlite_plugins.cracktools.guards import restore_game
        return restore_game(request,cancel,status)
    require_cli()
    from .backup import files
    files(root)  # Reject symlinks before following any manifest/config paths.
    appid=str(request.get('AppId',''))
    if not appid.isascii() or not appid.isdigit() or not 0<int(appid)<2**32:raise ValueError('Set a valid Steam App ID.')
    from .guest.vanilla import installed_baseline
    library=root.parents[2] if len(root.parents)>2 and root.parent.name=='common' and root.parent.parent.name=='steamapps' else None
    baseline=installed_baseline(root,appid,library)
    network=None
    job='playlite-autocrack-'+uuid.uuid4().hex
    try:
        if request.get('GenerateInfo'):
            from PyQt6.QtCore import QCoreApplication,QSettings
            app=QCoreApplication.instance() or QCoreApplication([])
            network=downloader_module('network').Network()
            network.name+='-autocrack-'+uuid.uuid4().hex[:8]
            settings=QSettings('Playlite','SteamDownloader')
            preferences=downloader_module('settings').Preferences(settings.value('country',''),settings.value('protocol','udp'))
            credentials=downloader_module('credentials').Wallet().read()
            def progress(message):
                if cancel():
                    network.cancel()
                    raise ValueError('Cancelled before processing game files.')
                status(message)
            network.connect(preferences,*credentials,progress=progress)
        with tempfile.TemporaryDirectory(prefix='playlite-autocrack-work-') as temporary:
            work=Path(temporary)
            (work/'baseline.json').write_text(json.dumps(baseline));(work/'baseline.json').chmod(0o600)
            args=container_args(root,work,job,network)
            payload=dict(request,GuestPath='/mnt/standalone/game')
            payload.pop('InstallDirectory',None);payload.pop('VmRoot',None)
            with tempfile.TemporaryFile() as log:
                child=subprocess.Popen(['docker',*args],stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT)
                folder=work/'jobs'/job.rsplit('-',1)[-1]
                last='';cancelling=False
                deadline=time.monotonic()+int(request.get('TimeoutMinutes',5))*60+180
                try:
                    child.stdin.write(json.dumps(payload).encode());child.stdin.close()
                    while child.poll() is None:
                        if cancel() and not cancelling:
                            cancelling=True;status('Cancelling CLI processing and restoring originals…')
                        if cancelling and folder.is_dir():(folder/'cancel').touch()
                        activity=folder/'activity.log'
                        if activity.is_file():
                            message=activity.read_text(errors='replace')
                            if message!=last:
                                status(message.replace('Steam VM','CLI container').replace('VM CLI','CLI').replace('VM processing','CLI processing'))
                                last=message
                        if time.monotonic()>deadline:raise ValueError('CLI worker timed out. Original backups were retained; restore them before retrying.')
                        time.sleep(.2)
                    result=folder/'result.json'
                    if not result.is_file():
                        log.seek(0,2);log.seek(max(0,log.tell()-4000))
                        detail=log.read().decode(errors='replace')
                        key=request.get('ApiKey','')
                        if key:detail=detail.replace(key,'[redacted]')
                        raise ValueError('The CLI container stopped before reporting a result. '+detail)
                    result=json.loads(result.read_text())
                    if child.returncode or not result.get('Success'):raise ValueError(result.get('Message','CLI processing failed. Backups were retained.'))
                    status('CLI processing complete. Originals are backed up for restoration.')
                    return dict(result,Message='CLI processing complete. Originals are backed up for restoration.')
                finally:
                    if child.poll() is None:
                        subprocess.run(['docker','rm','-f',job],capture_output=True,timeout=30)
                        try:child.wait(timeout=10)
                        except subprocess.TimeoutExpired:child.kill();child.wait()
    finally:
        if network is not None:network.disconnect()
