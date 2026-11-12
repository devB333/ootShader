# Technical notes

Findings behind SCB-OOT, checked on The Legend of Zelda: Ocarina of Time Remake (title
`0400486024dec000`, update 131072) with Lichii builds 119 and 125. Cache version 54.

## The cache file (`vulkan.fork-v54.bin`)

* Header: magic `yuzucach` and a u32 version (54).
* Record: `u32 count`, then `count` environments, then the key.
  * Environment (`GenericEnvironment::Serialize`): six u64 counts, seven u32 fields (the stage is at
    offset 72 from the start of the environment), the shader code, then the recorded tables
    (texture types, pixel formats, constant buffer values, storage pointer classifications, and for
    graphics the 0x50 byte program header).
  * Graphics key: 1,056 bytes = six u64 shader hashes (VertexA, VertexB, TessControl, TessEval,
    Geometry, Fragment) plus a 1,008 byte `FixedPipelineState`. Compute key: 24 bytes.
* Stage numbers: 0 VertexB, 1 TessControl, 2 TessEval, 3 Geometry, 4 Fragment, 5 Compute, 6 VertexA,
  7 Task, 8 Mesh.

## What makes a cache GPU specific

Three values inside `FixedPipelineState`: the Vulkan driver id (offset 16), the driver version (offset
20) and feature bits in `raw1` (offset 0): bit 0 extended dynamic state, 1 EDS2, 2 EDS2 logic op,
3 EDS3 blend, 4 EDS3 enables, 5 vertex input dynamic state, 20 color write enable. Records written
on another GPU have other keys and the emulator ignores them. `convert_cache.py` and the importer
rewrite these values. They can be read from `vulkaninfo` (driver id, packed `driverVersion`, and the
extension and feature lists, with the emulator's Extended Dynamic State level, default 2). On the
test PC the detected values equal what Lichii itself wrote. Differences in the logic op bit between
an NVIDIA and an AMD recording were harmless (32,410 of 32,410 records identical).

## Shader hashes

* For this game the emulator stores lowered Maxwell code produced from the native Ampere code
  (`LowerProgram` in Lichii's `warm_records.cpp`). The key hash of a stage is
  `CityHash64(whole environment code) ^ 0x4F554E4345000002`. For graphics the environment code starts
  with the first 80 bytes of the native file header, followed by the lowered instructions.
* Mesh and task pipelines: the hashes of slots 1 (task) and 3 (mesh) are also XORed with
  `MeshStateHash` (a hash of three mesh registers and a 20 word header). The cache holds 54 distinct
  values for this game. The importer finds the value by trying every mesh hash until both slots
  resolve.
* A mesh shader without a task shader sits in slot 1 (VertexB) with a stage 8 environment, also XORed
  with `MeshStateHash`. The value is `CityHash64(last 92 bytes of the mesh environment) | 1` (three mesh
  registers and the 20 word primitive header). 483 pipelines (29 shaders, 19 mesh states) have this shape.
* Pairing of vertex and fragment shaders from the game files: in `.bfsha` archives a vertex and a
  fragment program with the same variation number belong together. In `.sharcb` (BAHS) archives records
  alternate vertex, fragment, so variation v pairs with v+1. This covers every pair in the cache whose
  shaders are in the game files.

## Where the shaders are

`romfs/Shader/*.bfsha.zs`, `Layout2Shader`, `Effect/*.vsetb.byml.zs`, and the engine library archives
`romfs/Lib/agl`, `Lib/Terrain`, `Lib/gsys` (`*_resource.*.sarc.zs`). The library archives hold about
5,500 programs and nearly all of the shaders that first looked missing. Programs come out through
Lichii's `tools/nx2/extract_static_shaders.py`. Unreadable: eight containers (for example NDSR packages).
Not reproduced: 15 compute pipelines in the hints (none appears in a recorded used list). For 12 of them no
extracted compute program has their workgroup size and shared memory, so the programs are not in any
readable container. A scan of the other files in the `.nup` (large asset folders excluded) finds no shader
container outside the files that are read. `tools/diagnose_missing.py` produces this report.

The lowering must match the emulator build that recorded the hints. With Lichii `build-125` all recorded
graphics shaders are reproduced; `build-160` lowers 32 mesh shaders to other code (other size and hash), which
skips 369 task+mesh pipelines.

## Used pipelines list (`vulkan_used.fork-v54.bin`)

Magic `EUP1` (0x31505545), u32 count, then entries of u64 hash and u8 age (sessions since last use). The
hash is Lichii's `GraphicsPipelineCacheKey::Hash()`: CityHash64 over the 48 bytes of shader hashes plus
`FixedPipelineState::Size()` bytes of state. The size depends on `raw1`: transform feedback 1008, dynamic
vertex input and EDS3 blend 80, dynamic vertex input 112, extended dynamic state 368, otherwise 432.
Verified on 2,285 of 2,285 entries of a real list. At start, Lichii builds the pipelines on the list and
queues the rest for background building, unless `EDEN_NX2_PRELOAD_ALL_PIPELINES=1` is set. A 40 minute
session used about 2,300 of the 32,900 pipelines.

## Hints

`tools/hints.py` keeps keys (with the GPU stamp removed), environment headers and recorded tables, and
drops the shader code. File format `SHHINT02`: header, then an lzma compressed body with a state table
(884 distinct states, 14 of 252 words vary), deduplicated environment blobs, records, and a usage age
per record. 32,927 pipelines take 1.4 MB.

## Other games (tested)

* Super Mario Bros. Wonder (Switch 2 Edition): the hints format works, but its cache holds the Switch 1
  (`Nin_NX_NVN`, Maxwell) shader code verbatim, with the key hash `CityHash64(code without its last 16
  bytes)`. The extractor does not read those archives. Supporting it needs an NX extractor and a no
  lowering path.
* Star Fox: all game data is one 14 GB `romfs/data.zip` with 114,154 stored entries named by hash IDs; no
  known shader container was found.
* Format level test on caches of three other games (code taken from the cache itself): 975 of 979,
  489 of 489 and 180 of 180 records identical, so `hints.py` is game independent. The step that
  regenerates shader code is engine specific.

## Fritchii (cache version 50)

Checked against the Fritchii sources (commit f66ea63), not in the running emulator. Same magic, record framing,
graphics and compute keys, `FixedPipelineState` and dynamic-state feature rules as Lichii build-125. Differences:

* File `vulkan.fork-v50.bin`; a file with another version in its header is deleted at start.
* Environments have no read-properties entry (version 52 and later append one constant buffer entry with key
  `0xFFFFFFFF00000000`; 81 environments in the hints have it) and no mesh registers after the program header.
* Task/mesh pipelines are neither written nor loaded.
* The lowering is older: built against it (`build_code_store.py --lowering-src`), 72 graphics and 89 task/mesh
  store hashes differ from build-125 and 132 more programs fail to lower. For the pipelines the format can
  store this loses 6 graphics and 7 more compute pipelines.

## Open items

* A real run of the version 50 cache in Fritchii.

* Real play test of `--fast-start`.
* The 15 compute pipelines whose shaders are not reproduced (probably the NDSR packages).
* Hints recorded with a newer Lichii build (mesh lowering changed after build-125).
* Re-test when Lichii ships its new shader cache format.
* Draw states for pipelines nobody has recorded (the blend setting, color formats) cannot be derived from the
  game files so far.
