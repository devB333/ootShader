# Tools

* `scb_oot.py`: the guided tool for the Ocarina of Time Remake (see the top level README).
* `convert_cache.py`: re-stamp a cache written on another GPU (below).
* `hints.py`: code-free hints, export, merge, import (further below).
* `build_code_store.py`: regenerate the shader code of a game from its `.nup`.
* `cityhash.py`: pure Python CityHash64 matching Lichii's, used for pipeline key hashes.

# convert_cache.py

Makes a Vulkan pipeline cache written on another GPU usable on yours, for Yuzu-lineage Switch emulators
(Lichii, Eden; cache file `vulkan.fork-v54.bin`, magic `yuzucach`, version 54).

## Why a cache from someone else is ignored

Every graphics record ends with a state block that holds the driver id, the driver version and a few
dynamic-state feature bits of the machine that wrote it. On another GPU the keys differ, so the emulator
never finds the record. This tool rewrites those three values for your machine. Nothing else changes.

## Use

1. Get the stamp of your machine, either from the system or from a cache your emulator wrote:

   ```
   python3 convert_cache.py --detect-system --print-stamp
   ```

   Needs `vulkaninfo`. On Windows run `vulkaninfo > vkinfo.txt` yourself (some overlay layers make it
   hang when started from other tools) and pass `--vulkaninfo-file vkinfo.txt`.

2. Convert:

   ```
   python3 convert_cache.py downloaded/vulkan.fork-v54.bin --detect-system --out converted.bin
   ```

   With no stamp option, the stamp is taken from the last graphics record of the cache you give (so that
   file must contain records your emulator wrote). The output is a new file and an existing file is never
   overwritten or modified.

3. Install: with no cache for the game yet, copy `converted.bin` to the game's
   `cache/shader/<title id>/vulkan.fork-v54.bin`. With an existing cache, append
   (`cat existing.bin <(tail -c +13 converted.bin) > merged.bin`, the header is 12 bytes), after
   closing the emulator and keeping a backup. Recent Lichii builds remove duplicates on load.

## Options

| Option | Meaning |
|---|---|
| `--detect-system` / `--vulkaninfo-file F` | stamp from `vulkaninfo` |
| `--gpu N` | GPU number (emulator setting `vulkan_device`, default 0) |
| `--dyna-state 0-3` | emulator's Extended Dynamic State setting (Lichii desktop default 2) |
| `--no-vids` | emulator's vertex input dynamic state setting is off |
| `--driver-id` `--driver-version` `--features` | type the stamp in by hand |
| `--print-stamp` | only show the stamp |
| `--source F` | more caches to convert from (repeatable) |
| `--selftest` | check against records your own emulator wrote (needs a cache with your records) |

## What was verified

Game: The Legend of Zelda: Ocarina of Time. Target: RX 6700 XT, AMD proprietary 2.0.331, Lichii build 119.

- The stamp detected from `vulkaninfo` (id 1, version `0x80014b`, features `0x100023`) equals what the
  emulator wrote.
- A cache written only on NVIDIA (3 driver versions, features `0x100027`) was converted without any cache
  of the target. Of the 2,681 records the AMD emulator had written itself, 2,673 (99.7%) were reproduced
  byte for byte, 0 differed, and the other 8 pipelines were simply not in the NVIDIA cache.
- Compute records need no change and are copied.

## Limits

- Feature bits are computed from Lichii's rules and checked on one machine only. A wrong bit makes the
  emulator ignore the records silently. The tool warns when a source record differs from your features
  other than the logic-op bit (the one difference that was verified to be harmless).
- Records that your emulator would not build (other game versions, other settings) are just unused.
- Without a cache of your own, the tool cannot tell which records you already have, so it writes all of them.
- A converted cache avoids the first-time compile only for pipelines the donor played. It does not
  create pipelines nobody has played.
- Share the tool, not caches: caches contain game shader code.

Tests: `python3 -I tests/test_convert_cache.py`

# hints.py: share a few MB instead of a cache

`hints.py` keeps everything of a pipeline cache except the shader code: the pipeline keys (draw state and
shader hashes), the recorded environments (headers and the values the translator read) and no GPU
stamp. The shader code is regenerated on the user's machine from their own game files.

```
python3 hints.py export  vulkan.fork-v54.bin --title <title id> --out game.hints   # on a played cache
python3 hints.py merge   nvidia.hints amd.hints --out combined.hints
python3 hints.py info    game.hints
python3 hints.py import  game.hints --out vulkan.fork-v54.bin --gfx-dir <lowered graphics> \
        --mesh-dir <lowered task/mesh> --compute-file <generated compute> --detect-system
```

`import` needs the lowered shader code of the game. `build_code_store.py` makes it from the game's own
files in one command (about 45 seconds for Ocarina of Time; needs g++, a clone of Lichii and a Python
with the zstandard module the extractor wants):

```
python3 build_code_store.py --nup game.nup --title 0400486024dec000 --lichii <Lichii clone> \
        --extractor-python <python with backports.zstd> --work <new folder>
```

It reads the shader files from the `.nup`, runs Lichii's `extract_static_shaders.py`, builds the programs
in `harness/` against Lichii's own `LowerProgram`, and writes `<work>/store/{gfx,mesh,compute.bin}`.
The store is shader code from the game: keep it local.

Verified (Ocarina of Time update 131072, RX 6700 XT; code store built fresh from the `.nup` by `build_code_store.py`): hints exported from an NVIDIA-written cache of
32,908 pipelines are 1.45 MB. Imported with the stamp detected from the system and the game files only,
32,393 pipelines were rebuilt and all 32,393 are byte-identical to the cache Lichii accepted. The other
515 need shaders that the game files do not reproduce and are skipped. With the combined hints (NVIDIA and AMD)
and the fresh store: 32,463 of 32,965 rebuilt (skipped: 485 graphics, 17 compute); all 32,410 that also exist in
the cache Lichii accepted are byte-identical.

Limits: hints only cover pipelines that someone played. Hints contain pipeline state and recorded reads
but no instructions; whether to share them is the exporter's decision.
