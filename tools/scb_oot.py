#!/usr/bin/env python3
"""SCB-OOT: Shader Cache Builder for The Legend of Zelda: Ocarina of Time Remake (Switch 2).

One run does everything:
  1. checks that the .nup is the Ocarina of Time Remake, update 131072 (anything else is refused);
  2. reads the game's shader files from your .nup and regenerates the shader code locally (nothing leaves your PC);
  3. rebuilds a Vulkan pipeline cache for THIS PC's graphics driver from a small hints file;
  4. backs up the cache you already have, then installs the new one in the emulator's cache folder.

It refuses to run while the emulator is open and never overwrites anything without a backup.
Cache format: Lichii/Eden "vulkan.fork-v54.bin" (cache version 54).
"""
import argparse, datetime, hashlib, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import convert_cache as cc
import hints as H
import build_code_store as B
from nup_read import Nup

ROOT = os.path.dirname(HERE)
TITLE = '0400486024dec000'
UPDATE = 131072
GAME = 'The Legend of Zelda: Ocarina of Time Remake'
CACHE_NAME = 'vulkan.fork-v54.bin'
USED_NAME = 'vulkan_used.fork-v54.bin'
# Fritchii reads cache version 50: other file name, older record layout, no task/mesh pipelines, and its own
# (older) shader lowering, so the code store must be built with --lowering-src <Fritchii>/src.
# Target emulator: (cache version, data folder under %APPDATA% or the XDG data folder, needs --lowering-src).
#   lichii        Lichii and Eden: vulkan.fork-v54.bin in the "eden" folder.
#   fritchii      Fritchii releases (v5.8.x): the same cache, in the "fritchii" folder. Fritchii merges
#                 vulkan.fork-v54.bin into its own vulkan-frizti.fork-v54.bin at start.
#   fritchii-nx2  the older nx2 branch of the Fritchii repository (cache version 50, no task/mesh pipelines).
#   fritchii-nx2-mesh  that branch with the task/mesh cache patch (cache version 150).
TARGETS = {'lichii': (54, 'eden', False), 'fritchii': (54, 'fritchii', False),
           'fritchii-nx2': (50, 'eden', True), 'fritchii-nx2-mesh': (150, 'eden', True)}
EMULATORS = ('lichii.exe', 'fritchii.exe', 'fritchii', 'eden.exe', 'eden-cli.exe', 'yuzu.exe', 'yuzu-cmd.exe', 'lichii', 'eden', 'yuzu')


def say(msg=''):
    print(msg, flush=True)


def fail(msg):
    sys.exit(f'\nSTOPPED: {msg}')


def check_nup(path):
    if not os.path.isfile(path):
        fail(f'file not found: {path}')
    try:
        n = Nup(path)
    except SystemExit as ex:
        fail(f'this is not a .nup file ({ex})')
    found = [(f'{p["title"]:016x}', p['version'], p['kind']) for p in n.packages]
    ok = any(t[:-3] == TITLE[:-3] and v == UPDATE and kind == 1 for t, v, kind in found)
    if not ok:
        fail(f'this .nup is not {GAME} update {UPDATE}.\n'
             f'  packages found: {found}\n'
             f'  expected a package of title {TITLE} with version {UPDATE}. Other games and updates are not supported yet.')
    say(f'game file OK: {GAME}, update {UPDATE}')


def default_cache_dir(folder='eden'):
    if os.name == 'nt':
        base = os.environ.get('APPDATA')
        if not base:
            fail('cannot find %APPDATA%; pass --cache-dir')
        return os.path.join(base, folder, 'cache', 'shader', TITLE)
    base = os.environ.get('XDG_DATA_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'share')
    return os.path.join(base, folder, 'cache', 'shader', TITLE)


def emulator_running():
    try:
        if os.name == 'nt':
            out = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, text=True, errors='replace').stdout.lower()
            return [e for e in EMULATORS if e.endswith('.exe') and f'"{e}"' in out]
        out = subprocess.run(['ps', '-A', '-o', 'comm='], capture_output=True, text=True, errors='replace').stdout.split()
        found = [e for e in EMULATORS if not e.endswith('.exe') and e in out]
        if shutil.which('tasklist.exe'):                         # WSL: the emulator is a Windows program
            win = subprocess.run(['tasklist.exe', '/FO', 'CSV', '/NH'], capture_output=True, text=True, errors='replace').stdout.lower()
            found += [e for e in EMULATORS if e.endswith('.exe') and f'"{e}"' in win]
        return found
    except OSError:
        return []


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(1 << 22):
            h.update(chunk)
    return h.hexdigest()


def count_records(path, version=54):
    import mmap
    with open(path, 'rb') as f:
        m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        g = c = 0
        end = 12
        for off, eend, en, comp in cc.read_records(m, version):
            c += comp; g += not comp; end = en
        if end != len(m):
            raise cc.CacheError('the file has trailing bytes')
        return g, c


def free_gb(path):
    while not os.path.exists(path):
        path = os.path.dirname(path) or '.'
    return shutil.disk_usage(path).free / 1e9


def ask(question, assume_yes):
    if assume_yes:
        say(f'{question} -> yes (--yes)')
        return True
    try:
        return input(f'{question} [y/N] ').strip().lower() in ('y', 'yes')
    except EOFError:
        return False


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--nup', required=True, help=f'your {GAME} .nup file (update {UPDATE})')
    ap.add_argument('--hints', default=os.path.join(ROOT, 'hints', 'combined.hints'), help='hints file (default: the bundled one)')
    ap.add_argument('--cache-dir', help=f'emulator cache folder for the game (default: the Eden/Lichii folder, ...\\{TITLE})')
    ap.add_argument('--yes', action='store_true', help='do not ask for confirmation (a backup is still made)')
    ap.add_argument('--fast-start', type=int, metavar='AGE', help='EXPERIMENTAL: also write a starter "used" list so the first start builds only '
                    'recently used pipelines (0 = about 2,200 pipelines, 1 = about 11,000); the rest is built in the background')
    ap.add_argument('--keep-work', action='store_true', help='keep the temporary shader-code folder (it contains game code)')
    ap.add_argument('--lichii', default=os.path.join(ROOT, 'lichii'), help='Lichii folder with tools/nx2 (bundled by default)')
    ap.add_argument('--bin-dir', default=os.path.join(ROOT, 'bin'), help='ready-made lowering programs (bundled by default)')
    ap.add_argument('--extractor-python', default=sys.executable)
    ap.add_argument('--emulator', choices=sorted(TARGETS), default='lichii',
                    help='emulator to build the cache for: lichii (default, also Eden), fritchii (Fritchii v5.8.x releases: same '
                    'cache, installed in the fritchii data folder), fritchii-nx2 / fritchii-nx2-mesh (the older nx2 branch of '
                    'Fritchii, cache versions 50 / 150; they need --lowering-src)')
    ap.add_argument('--lowering-src', help='with --emulator fritchii-nx2 or fritchii-nx2-mesh: the src folder of that Fritchii checkout '
                    '(its shader lowering decides the shader hashes); --lichii must then be Lichii build-125')
    H.machine_args(ap)
    a = ap.parse_args()
    version, data_folder, needs_lowering = TARGETS[a.emulator]
    cache_name, used_name = (n.replace('v54', f'v{version}') for n in (CACHE_NAME, USED_NAME))
    if needs_lowering:
        if not a.lowering_src:
            fail(f'--emulator {a.emulator} needs --lowering-src <Fritchii checkout>/src (and --lichii <Lichii build-125>).')
        if a.fast_start is not None:
            fail('--fast-start is not supported for this target.')
        a.lowering_src = os.path.abspath(a.lowering_src)

    say(f'SCB-OOT: Shader Cache Builder for {GAME}\n')
    a.nup, a.hints, a.lichii, a.bin_dir = (os.path.abspath(x) for x in (a.nup, a.hints, a.lichii, a.bin_dir))
    a.extractor_python = os.path.abspath(shutil.which(a.extractor_python) or a.extractor_python)
    check_nup(a.nup)
    try:
        head, records, _ = H.load(a.hints)
    except (H.HintError, OSError) as ex:
        fail(f'cannot read the hints file {a.hints}: {ex}')
    if head.get('title_id') != TITLE or head.get('cache_version') != 54:
        fail(f'the hints are for title {head.get("title_id")}, cache version {head.get("cache_version")}; need {TITLE}, 54')
    say(f'hints OK: {len(records)} pipelines')

    cache_dir = os.path.abspath(a.cache_dir) if a.cache_dir else default_cache_dir(data_folder)
    if not os.path.isdir(os.path.dirname(cache_dir)):
        fail(f'the emulator cache folder {os.path.dirname(cache_dir)} does not exist.\n'
             '  Start the emulator once, or pass --cache-dir with the right folder.')
    running = emulator_running()
    if running:
        fail(f'close the emulator first (running: {", ".join(sorted(set(running)))}).')
    try:
        machine, desc = H.machine_from_args(a) if (a.detect_system or a.vulkaninfo_file or a.driver_id is not None) else \
            cc.machine_from_vulkaninfo(cc.run_vulkaninfo(), a.gpu, a.dyna_state, not a.no_vids)
    except (cc.CacheError, H.HintError) as ex:
        fail(f'cannot detect the graphics driver: {ex}\n  Save "vulkaninfo > vkinfo.txt" and pass --vulkaninfo-file vkinfo.txt, or give the stamp by hand.')
    say(f'graphics driver: {desc} (driver id {machine[0]}, version {machine[1]:#x}, features {machine[2]:#x})')
    say(f'cache folder: {cache_dir}')

    tmp = tempfile.mkdtemp(prefix='scb-oot-')
    need = 4.0
    if free_gb(tmp) < need or free_gb(cache_dir) < 3.5:
        shutil.rmtree(tmp, ignore_errors=True)
        fail(f'not enough free disk space (need about {need:.0f} GB for the temporary files and {3.5:.1f} GB in the cache folder).')
    try:
        say('\nStep 1 of 3: regenerating the shader code from your game file (about a minute)')
        store = B.build_store(a.nup, os.path.join(tmp, 'work'), a.lichii, a.bin_dir, a.extractor_python, TITLE, a.lowering_src)
        say('\nStep 2 of 3: building the cache for your graphics driver')
        new_cache = os.path.join(tmp, cache_name)
        new_used = os.path.join(tmp, used_name) if a.fast_start is not None else None
        ok, total, why, starter = H.run_import(a.hints, new_cache, os.path.join(store, 'gfx'), os.path.join(store, 'mesh'),
                                               os.path.join(store, 'compute.bin'), version, machine, new_used, a.fast_start or 0)
        total -= why.get(H.NOT_STORED, 0)                                             # not a failure: the format has no place for them
        if ok < 0.9 * total:
            fail(f'only {ok} of {total} pipelines could be rebuilt; something is wrong with the game files or the hints. Nothing was changed.')
        g, c = count_records(new_cache, version)
        say(f'new cache checked: {g} graphics + {c} compute pipelines, {os.path.getsize(new_cache) / 1e9:.2f} GB')
        if not a.keep_work:
            shutil.rmtree(os.path.join(tmp, 'work'), ignore_errors=True)                  # game code: delete as soon as possible

        say('\nStep 3 of 3: installing')
        os.makedirs(cache_dir, exist_ok=True)
        target, target_used = os.path.join(cache_dir, cache_name), os.path.join(cache_dir, used_name)
        existing = [p for p in (target, target_used) if os.path.exists(p)]
        if existing:
            size = os.path.getsize(target) / 1e9 if os.path.exists(target) else 0
            say(f'an existing cache was found ({size:.2f} GB). It will be backed up and then replaced.')
            if not ask('Replace it?', a.yes):
                fail('cancelled. Nothing was changed.')
            backup = os.path.join(cache_dir, 'scb-oot-backup-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
            if free_gb(cache_dir) < size + os.path.getsize(new_cache) / 1e9 + 0.5:
                fail('not enough free disk space for the backup. Nothing was changed.')
            os.makedirs(backup)
            for p in existing:
                shutil.copy2(p, backup)
                if os.path.getsize(p) != os.path.getsize(os.path.join(backup, os.path.basename(p))) or \
                        sha256(p) != sha256(os.path.join(backup, os.path.basename(p))):
                    fail('the backup did not verify. Nothing was changed.')
            say(f'backup verified: {backup}')
        for src, dst in ((new_cache, target), (new_used, target_used)):
            if src and os.path.exists(src):
                part = dst + '.scb-new'
                shutil.copy2(src, part)
                os.replace(part, dst)
        g2, c2 = count_records(target, version)
        say(f'installed: {target}\n  {g2} graphics + {c2} compute pipelines')
    finally:
        if a.keep_work:
            say(f'temporary files kept in {tmp} (they contain game code; delete them yourself)')
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    say('\nDone. Start the emulator and load the game.')
    if a.fast_start is None:
        say('The first start builds every pipeline, which can take 45+ minutes. Let it finish.\n'
            'It only does that if the emulator has EDEN_NX2_PRELOAD_ALL_PIPELINES=1 (nx2_switches.txt); otherwise it builds some now and the rest later.')
    else:
        say(f'Fast start (experimental): the first start builds about {starter} pipelines and prepares the rest in the background.')
    stored = why.pop(H.NOT_STORED, 0)
    if stored:
        say(f'{stored} task/mesh pipelines are left out: cache version {version} cannot store them, the emulator builds them in every session.')
    say(f'Skipped {sum(why.values())} pipelines that the game files do not reproduce; they are built when the game first needs them.')


if __name__ == '__main__':
    try:
        main()
    except (H.HintError, cc.CacheError) as ex:
        sys.exit(f'\nSTOPPED: {ex}')
