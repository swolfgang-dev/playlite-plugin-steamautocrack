# Native SteamAutoCrack

The enabled `SteamAutoCrack` generic plugin replaces the old Wine/Windows CLI
bridge. It retains the plugin ID, saved username/API key, per-add-method defaults,
and Run action. All new engine, tool setup, and progress code belongs to the
plugin directory. `playlite.steam_runner` is a compatibility entry point only.
No existing Wine installation is needed or read.

## Setup and use

Settings → Plugins → Installation → SteamAutoCrack contains username, masked
Steam Web API key, timeout, tool paths, game-info generation, and SteamStub
unpacking options. Install / update native tools downloads the Windows and Linux
Goldberg emulator libraries from the official
[gbe_fork releases](https://github.com/Detanup01/gbe_fork/releases), downloads a
SHA-512-verified Linux .NET 10 SDK from Microsoft, and locally builds the
Steamless unpackers from the original
[SteamAutoCrack sources](https://github.com/SteamAutoCracks/Steam-auto-crack).
Native 7-Zip must be available as `7z` or `7zz`. Alternatively choose existing
emulator libraries and the native unpacker with the file/folder pickers.
Tools use `$XDG_DATA_HOME/playlite/steamautocrack/tools`; jobs use
`$XDG_STATE_HOME/playlite/steamautocrack`. Standard Linux defaults apply when
these environment variables are absent.

Set the game's Steam metadata ID, then choose SteamAutoCrack: Run in its context
menu or Play dropdown. Game-info generation writes achievement definitions,
stats, and known DLC configuration using Steam's store/Web APIs. Disable this
option to process without an API key. Per-method run-after-add defaults remain
optional; new defaults are off. Saved explicit preferences are retained.

Both 32-bit and 64-bit Windows Steam API libraries and x86/x64 native Linux Steam
API libraries are supported. The native .NET helper includes SteamStub variants
1.0, 2.0, 2.1, 3.0, and 3.1 (x86), and 3.0/3.1 (x64). It reads PE files as data;
it does not execute game binaries. SteamStub unpacking applies to Windows PE
executables; ELF DRM unpacking is not implemented. The helper keeps the `.bind`
section and disables Windows-only checksum recalculation. Optional original
features such as experimental emulator/overlay injection, inventory generation,
achievement image downloads, and crack-only ZIP export are not implemented.

## Originals and cancellation

Processing stages configuration and unpacking before touching originals. A
per-game file lock prevents concurrent native runs and Playlite launches during
processing. Running/launching games are rejected, including fresh Lutris process
checks. Original files and SHA-256 checksums are saved in the installation's
`.playlite-steamautocrack` directory. The manifest is committed before applying
changes, allowing interrupted operations to be restored later. A prior backup
must be restored before applying again.

Cancellation, timeout, or failure during application restores verified originals.
Existing settings files are backed up individually; unrelated configuration and
save files are retained. Restore originals verifies all backup hashes and refuses
to overwrite files changed by the user since processing. API keys are removed
from the private job request before processing and never included in progress
messages or error URLs. Saved credential files are owner-readable/writable only.

## Upstream sources and licenses

The original SteamAutoCrack orchestration is MIT licensed. Its documented flow
(game information, configuration, unpacking, emulator application, restore)
informs the native Python implementation. The native helper uses unchanged
Steamless sources downloaded at commit
`f8b93bc2ae76ce3348cc98fd3af8f2b0521c0967`, compiled locally with a separate
Playlite console entry point. Upstream unpacker code is not vendored into this
repository or distributed as a built plugin asset.

Steamless is copyright atom0s and uses
[CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/).
Its attribution/license accompanies the locally built helper. Goldberg/gbe_fork
has its own upstream GPL license and distribution notices, preserved in the
extracted tool archives. Third-party tools retain their respective licenses.
