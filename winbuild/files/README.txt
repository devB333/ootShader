SCB-OOT: Shader Cache Builder for The Legend of Zelda: Ocarina of Time Remake
=============================================================================

What it does
  Builds a Vulkan shader cache for YOUR PC from your own game file (.nup) plus a small "hints" file
  (1.4 MB, no game code inside). Meant for Lichii and Eden (cache file vulkan.fork-v54.bin,
  cache version 54). The cache is written for your graphics card and driver, whatever card the
  hints were recorded on. The goal is fewer shader stutters the first time you play.

Supported
  Only The Legend of Zelda: Ocarina of Time Remake (Switch 2), title 0400486024dec000,
  update 131072. Any other game or update is refused.

How to use
  1. Close the emulator.
  2. Double-click scb-oot.bat (or drag your game .nup file onto it) and follow the questions.
     It checks the game file, rebuilds the shader code locally (1 to 10 minutes, depending on
     antivirus scanning; nothing leaves your PC), builds the cache for your graphics driver, backs up the cache you already have,
     and installs the new one in %APPDATA%\eden\cache\shader\0400486024dec000\
  3. Start the emulator and load the game. The first start builds the pipelines and can take
     45 minutes or more. Let it finish. In Lichii this full build only happens if
     EDEN_NX2_PRELOAD_ALL_PIPELINES=1 is set (nx2_switches.txt in %APPDATA%\eden).

Needs about 4 GB of free disk space while it runs (temporary files are deleted at the end) and
about 2 GB in the cache folder, plus the same again for the backup of an existing cache.

Options (add them after the .nup path in a Command Prompt, or edit scb-oot.bat)
  --cache-dir FOLDER        use another cache folder
  --yes                     do not ask before replacing (a verified backup is still made)
  --fast-start 0            EXPERIMENTAL: also write a starter "used" list so the first start builds
                            about 2,200 pipelines instead of all of them. Not yet tested in play.
  --dyna-state 3 / --no-vids   if you changed the emulator's Extended Dynamic State or
                            Vertex Input Dynamic State settings
  --vulkaninfo-file FILE    if vulkaninfo cannot be run: save "vulkaninfo > vkinfo.txt" and pass it
  --keep-work               keep the temporary shader-code folder (it contains game code)

Safety
  - It refuses to run while the emulator is open.
  - An existing cache is copied to a "scb-oot-backup-<date>" folder inside the cache folder and
    the copy is verified before anything is replaced. To go back, copy the files from the backup
    folder over the new ones.
  - It reads your .nup and never changes it.

Limits
  - Tested on one PC (Windows 11, RX 6700 XT, Lichii builds 119 and 125). A wrong graphics-driver
    stamp makes the emulator silently ignore the cache. If the emulator seems not to use it, compare
    the "Report: Driver:" lines of its log with what this tool printed.
  - A new emulator version may change the cache format; then this needs an update.
  - About 500 of the 32,900 pipelines (1.5 percent) need shaders that the game files do not
    reproduce. They are skipped and built when the game first needs them.
  - The hints only cover pipelines that someone played, so some hitches remain.
  - The two .exe helpers are not code-signed; Windows may warn about them.

Other tools are in the tools folder (see tools\README.md). License: GPL-3.0 (LICENSE-GPL-3.0.txt).
See NOTICE.txt.
