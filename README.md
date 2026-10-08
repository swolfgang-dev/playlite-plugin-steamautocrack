# SteamAutoCrack for Playlite

Process Windows games with the Windows SteamAutoCrack CLI inside the Steam
Downloader VM. Playlite sends jobs through the QEMU guest agent; game files stay
in the shared Steam library and are accessed as `S:\` inside Wine.

Requires Playlite 0.2.61 or later and Steam Downloader 0.1.11 or later.

## Setup

1. Set up the Steam Downloader VM with Wine and Steam Auto Crack installed.
2. Install this plugin and use **Set up / check VM CLI** in its settings.
3. Choose the VM userdata folder. The current Playlite profile's Steam VM is
   selected by default; another owned Steam VM can be selected explicitly.
4. Set your emulator username and, for game-info generation, your Steam Web API
   key. Game-info requests connect the VM VPN if needed, then verify its
   connection and kill switch before processing. NordVPN must already be signed in.

The plugin installation hook offers CLI setup. Setup downloads checksum-verified
.NET SDK and source archives inside the VM and builds upstream 3.5.1.0 for
Windows x86. The Linux SDK is only a build tool in the guest; processing uses
`SteamAutoCrack.CLI.exe` under Wine and the installed Windows .NET runtime.
The CLI uses the GUI installation's Goldberg emulator libraries. The plugin
contains no native Linux emulator processing or Steamless implementation.

The upstream release ZIP contains only the GUI. Its separate CLI project has
three option-description constructor calls incompatible with its own
System.CommandLine 2.0 dependency. The build recipe changes those descriptions
to property initializers and initializes the Steam app list before online processing,
which prevents DLC generation from waiting indefinitely. Version,
source commit, recipe revision, and the upstream license are retained in the
VM CLI installation.

## Processing and recovery

Right-click an installed game and choose **SteamAutoCrack → Run**. Games must
have a Steam metadata ID and be inside the selected VM's shared library.
Archived games and game folders containing symlinks are excluded. Windows
Steam API DLLs are required. Stop the game before modifying its files.

The existing **Run SteamAutoCrack after adding** option is retained. Ctrl-click
or Shift-click games, then use the selection action to process a sequential
batch with per-game results and cancellation.

Before running the CLI, the guest saves verified copies of executable/library
files and existing `steam_settings` content in
`<game>/.playlite-steamautocrack/`. CLI errors, timeouts, and cancellation restore
those originals and remove newly generated processing files. If recovery cannot finish,
backups remain available. **Restore originals** preserves files that
changed after processing without first preserving them in the backup folder’s
`recovery/` subfolder. Previous processing or archive installations are restored automatically before a new run.
Legacy backups made by the native plugin remain recoverable, including games
outside the shared VM library. Old native tool downloads are left in user data
and are no longer used.

Processing and restoration dialogs show a live log with a **Copy log** button.
Logs remain in the displayed host job folder after the dialog closes, including
guest CLI output and error details. Steam API keys are redacted.

Host and guest job files are private. API keys travel in bounded JSON through
guest-agent stdin, are never command-line arguments, are redacted from progress
logs, and are removed from temporary requests/configuration after each job.
Processing failures are detected from both the CLI's exit code and its output,
since upstream may log an error while returning zero.

## Development and distribution

Build a standalone plugin archive with `python3 tools/build_release.py`.
`dist/plugin.zip` includes the VM adapter and guest worker/setup recipes.
Install/update from this archive in Playlite, then restart Playlite.
Tag the manifest version (`v3.1.4`) to publish through the release workflow.

With Playlite and PyQt6 available, run:

```sh
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -q
```

Tests load the current checkout and cover VM path mapping, job submission,
cancellation, credential redaction, CLI failure detection, rollback, legacy
restoration, settings migration, and sequential batch progress.

CLI `.dll.bak` and `.exe.bak` files are collected under the managed backup
folder’s `cli/existing/` and `cli/generated/` directories. Previous emulator
settings are preserved there too. Applied emulator DLLs are checked against
the installed emulator libraries, and leftover unpacked outputs cause failure.
Restoration returns the pre-run files, including any pre-existing `.bak` files.

New manifests record every file and directory created during processing. Restore
removes those files and prunes their empty directories, recreates removed original
directories, and restores only original files changed by processing. Untouched
files and unrelated files added after processing remain unchanged. Older manifests
retain their original, more limited recovery coverage.

Shared backup, restoration, progress and game-file guards are provided by Crack Tools (0.1.0 or later). Steam Downloader remains required to manage the VM and VPN.
