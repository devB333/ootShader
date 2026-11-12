# SCB-OOT

**Shader Cache Builder for The Legend of Zelda: Ocarina of Time Remake (Switch 2)**, for the Lichii and Eden
emulators.

The first time a game draws something new, the emulator has to compile a shader pipeline, and the game
stutters. SCB-OOT builds the shader cache for your own graphics card **before you play**, from your own
game file plus a small hints file (about 1.4 MB). No shader code is distributed, and nothing leaves your PC.

> Status: first version, tested on one PC (Windows 11, AMD RX 6700 XT, Lichii builds 119 and 125).
> Supports **only** title `0400486024dec000`, update `131072`. Other games and updates are refused.

## How it works

1. It checks that your `.nup` is the Ocarina of Time Remake, update 131072.
2. It reads the game's shader files from the `.nup` (no keys needed) and regenerates the shader code
   with Lichii's own lowering code, in a temporary folder that is deleted at the end.
3. It rebuilds a pipeline cache (`vulkan.fork-v54.bin`, cache version 54) from `hints/combined.hints`,
   stamped for **your** GPU and driver (read from `vulkaninfo`).
4. It backs up any cache you already have, verifies the backup, and installs the new cache.

The hints contain pipeline states, shader hashes and the values the emulator recorded while someone
played (constant-buffer values, texture types and formats). They contain no shader instructions. They
were recorded on an NVIDIA machine and an AMD machine; one file works for any GPU, because the
GPU-specific part (driver id, driver version and three feature bits) is written at import time.

## Results so far

* Of the 32,927 pipelines in the hints, 32,912 (99.95%) can be rebuilt from the game files. The other 15 are
  compute pipelines whose shaders are in containers the extractor cannot read; the emulator builds them
  when first needed. `tools/diagnose_missing.py` explains every skipped pipeline.
* A cache converted from an NVIDIA-only recording ran in Lichii v125 on an AMD card: all records loaded,
  41 of 32,908 (0.12%) were removed by the emulator for missing recorded values, and a 40 minute play
  session built 16 pipelines at runtime (the longest 109 ms).
* The rebuilt records are byte-identical to the records the emulator itself accepted (32,410 of 32,410
  that exist in both).
* The first start after installing the cache can take 45 minutes or more while the emulator builds the
  pipelines. This is a one time cost.

## Use it (Windows)

Download the `scb-oot-win64.zip` bundle from the Releases page, unzip it, close the emulator, and run
`scb-oot.bat` (you can drag your `.nup` file onto it). The bundle contains its own Python and two small
helper programs, so nothing needs to be installed. Windows may warn about the unsigned `.exe` files.

## Use it (Linux, or from source)

You need Python 3.14 (or 3.13 with `pip install backports.zstd`), `g++` with C++20, and a clone of
[Lichii](https://github.com/zortos293/Lichii) at the build the hints were recorded with (`build-125`; newer
builds such as `build-160` lower some mesh shaders to other code, and 369 more pipelines are skipped):

```
python3 tools/scb_oot.py --nup "The Legend of Zelda Ocarina of Time [0400486024DEC000][v131072].nup" \
    --lichii /path/to/Lichii --hints hints/combined.hints
```

Useful options: `--cache-dir`, `--yes`, `--fast-start 0` (experimental, see below), `--dyna-state`,
`--no-vids`, `--vulkaninfo-file`. See `python3 tools/scb_oot.py --help`.

## Fritchii v5.8.x releases (cache version 54)

The Fritchii releases (v5.8.4, branch `oot-gather-fix`) use the same cache as Lichii: `vulkan.fork-v54.bin`, with
task/mesh pipelines, in `%APPDATA%\fritchii\cache\shader\<title id>` (or `user\cache\shader\...` next to the
exe in portable mode). Fritchii keeps its own file (`vulkan-frizti.fork-v54.bin`) and merges `vulkan.fork-v54.bin`
from the same folder into it at start. Use the Lichii build-125 checkout (the release's own `tools/nx2` extractor is
older and does not take the zstd dictionary; its lowering gives the same code for every shader in the hints):

```
python3 tools/scb_oot.py --nup game.nup --lichii /path/to/Lichii-build-125 \
    --cache-dir "/mnt/c/Users/<you>/AppData/Roaming/fritchii/cache/shader/0400486024dec000" \
    --vulkaninfo-file vkinfo.txt
```

With the helpers built against v5.8.4 (commit 4f5c531) the same 32,912 of 32,927 pipelines are rebuilt as with
Lichii build-125. Run the emulator once first so the folder exists. Not yet loaded in a v5.8.4 build.

The sections below are about the older `nx2` branch of the Fritchii repository (cache version 50), which the
releases are not built from.

## Fritchii `nx2` branch (experimental)

[Fritchii](https://github.com/Frizti/Fritchii) reads cache version 50 (`vulkan.fork-v50.bin`) and lowers some shaders
differently from Lichii. Build the cache with its own lowering code:

```
python3 tools/scb_oot.py --nup game.nup --emulator fritchii \
    --lichii /path/to/Lichii-build-125 --lowering-src /path/to/Fritchii/src
```

Of the 32,927 pipelines in the hints, 29,086 are written: 25,941 of the 25,947 vertex+fragment pipelines and 3,145 of
the 3,167 compute pipelines. The 3,813 task/mesh pipelines are left out, because Fritchii's cache format has no place
for them and Fritchii builds them in every session. Pipeline keys and the driver stamp rules are the same as Lichii's.

A Fritchii build with the task/mesh cache patch (branch `cache-task-mesh-pipelines` of a fork, cache version 150,
`vulkan.fork-v150.bin`) stores them too: use `--emulator fritchii-mesh`. That writes 32,263 pipelines (29,118
graphics, 3,145 compute); 636 task/mesh pipelines are skipped because Fritchii lowers their mesh shader differently.

### Example command (patched Fritchii, cache version 150)

Close the emulator first, then in WSL:

```
cd /path/to/ootShader
python3 tools/scb_oot.py --emulator fritchii-mesh --nup "/path/to/game.nup" --lichii ~/.cache/scb-oot/Lichii-125 --lowering-src fritchi/src --cache-dir /mnt/c/Users/<you>/AppData/Roaming/eden/cache/shader/0400486024dec000 --vulkaninfo-file /path/to/vkinfo.txt
```

`vkinfo.txt` comes from `vulkaninfo > vkinfo.txt` in PowerShell; make it again after a graphics driver update.

## Experimental: fast start

`--fast-start 0` also writes a starter "used pipelines" list so the emulator builds only about 2,200
pipelines at the first start and prepares the rest in the background. The list was verified against a real
session, but a real start with it has **not been tested** yet.

## Other tools

* `tools/convert_cache.py` re-stamps a cache written on another GPU so your emulator accepts it.
* `tools/hints.py` exports, merges and imports hints. Anyone with a played cache can contribute hints.
* `tools/build_code_store.py` regenerates the shader code from a `.nup`.
* `tools/diagnose_missing.py` reports which pipelines cannot be rebuilt, which shader is missing and why.

See `tools/README.md` and `docs/NOTES.md` for the technical details.

## Limits

* One game, one update. Other games need engine specific work (see `docs/NOTES.md`).
* A wrong driver stamp makes the emulator silently ignore the cache. Check the "Report: Driver" lines in
  the emulator log against what the tool printed.
* A new emulator version may change the cache format.
* The hints only cover pipelines that someone played.

## Licenses and credits

GPL-3.0-or-later (see `LICENSE`). The tools call extraction code from the
[Lichii](https://github.com/zortos293/Lichii) project (GPL-3.0), which builds on Eden, yuzu and others, and
the helper programs are built against Lichii's own sources. The Windows bundle includes the unmodified
Python embeddable package from python.org.

This project is not affiliated with Nintendo, Lichii or Eden. You need your own copy of the game.
