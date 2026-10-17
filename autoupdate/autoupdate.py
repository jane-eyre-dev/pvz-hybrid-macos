#!/usr/bin/env python3
"""PvZ Hybrid (植物大战僵尸杂交版) → native macOS auto-updater.

check version API → bilibili post → Quark share → save to user's Quark drive → download via OpenList →
extract → preflight → generate Godot shell project → export macOS + Linux → Linux smoke test + arm64 load test
→ assemble/sign/package → publish to ~/pvzhe/out.

Usage:
  autoupdate.py                    # normal (cron) run: does nothing if already built the latest version
  autoupdate.py --force            # rebuild even if version unchanged
  autoupdate.py --game-dir DIR     # skip download, build from an extracted Windows build
  autoupdate.py --no-publish       # build + test, but don't touch ~/pvzhe/out
"""
import argparse
import datetime
import fcntl
import glob
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pvzhe_net as net  # noqa: E402

HOME = os.path.expanduser("~")
WORK = f"{HOME}/pvzhe/work"
TOOLS = f"{HOME}/pvzhe/tools"
OUT = f"{HOME}/pvzhe/out"
AU = f"{WORK}/autoupdate"
STATE = f"{AU}/state.json"
DL_DIR = f"{HOME}/quark-downloads"
TMP = f"{WORK}/tmp"
GDRE = f"{TOOLS}/gdre/gdre_tools.x86_64"
RCODESIGN = f"{TOOLS}/rcodesign/apple-codesign-0.29.0-x86_64-unknown-linux-musl/rcodesign"
DOTNET_DIR = f"{TOOLS}/dotnet"
TEMPLATES_ROOT = f"{HOME}/.local/share/godot/export_templates"
BUNDLE_ID = "io.github.local.pvzhe"
PKG_NAME = "PvZ杂交版-mac.tar.gz"
MIN_MACOS = "12.0"  # the bundled .NET 9 runtime dylibs are built with minos 12.0

ENV = dict(os.environ, TMPDIR=TMP, DOTNET_ROOT=DOTNET_DIR, PATH=f"{DOTNET_DIR}:{os.environ.get('PATH', '')}",
           DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1")


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def run(cmd, **kw):
    kw.setdefault("env", ENV)
    kw.setdefault("check", True)
    return subprocess.run(cmd, **kw)


def load_state():
    try:
        return json.load(open(STATE))
    except FileNotFoundError:
        return {}


def save_state(st):
    json.dump(st, open(STATE + ".tmp", "w"), ensure_ascii=False, indent=2)
    os.replace(STATE + ".tmp", STATE)


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


# ---------------------------------------------------------------- acquire

def acquire_latest():
    ver, post_url, full = net.latest_pc_version()
    known_non_pc = {full.get(k, {}).get("url", "").rsplit("/", 1)[-1] for k in ("android", "pvzhe-launcher")} - {""}
    sid, passcode, _ = net.windows_share_from_post(post_url, known_non_pc)
    stoken, info, files = net.share_list(sid, passcode)
    archives = [f for f in files if not f.get("dir") and re.search(r"\.(zip|7z|rar)$", f["file_name"], re.I)]
    if len(archives) != 1:
        raise RuntimeError(f"expected exactly one archive in share {sid}, got {[f['file_name'] for f in files]}")
    arc = archives[0]
    log(f"latest PC version {ver}; post {post_url}; share {sid} → {arc['file_name']} ({arc['size']:,} bytes)")
    return ver, post_url, sid, passcode, stoken, arc


def ensure_in_drive(sid, stoken, arc):
    token = net.openlist_token()
    ol_dir = f"{net.QUARK_MOUNT}/{net.SHARE_DIR_NAME}"
    def present():
        return any(i["name"] == arc["file_name"] and i["size"] == arc["size"] for i in net.ol_list(token, ol_dir))
    if present():
        log("archive already in Quark drive")
    else:
        cookie = net.quark_cookie()
        net.save_share(cookie, sid, stoken, [arc], net.share_dir_fid(cookie))
        log("saved share into Quark drive (来自：分享)")
        for _ in range(30):
            if present():
                break
            time.sleep(5)
        else:
            raise RuntimeError("saved file did not show up in OpenList")
    dest = f"{DL_DIR}/{arc['file_name']}"
    if os.path.exists(dest) and os.path.getsize(dest) == arc["size"]:
        log(f"already downloaded: {dest}")
    else:
        log("downloading via OpenList ...")
        net.ol_download(token, f"{ol_dir}/{arc['file_name']}", dest, arc["size"])
        log(f"downloaded {dest}")
    return dest


def extract(archive):
    stem = re.sub(r"\.(zip|7z|rar)$", "", os.path.basename(archive), flags=re.I)
    dest = f"{DL_DIR}/unzipped/{stem}"
    if os.path.isdir(dest) and find_game_dir(dest, quiet=True):
        log(f"already extracted: {dest}")
        return dest
    tmp = dest + ".partial"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    if archive.lower().endswith(".zip"):
        root = os.path.realpath(tmp)
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                name = info.filename
                if not info.flag_bits & 0x800:            # not UTF-8 flagged: Chinese zips are GBK
                    try:
                        name = name.encode("cp437").decode("gb18030")
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        pass
                target = os.path.realpath(os.path.join(root, name))
                if not target.startswith(root + os.sep):
                    raise RuntimeError(f"unsafe path in archive: {name}")
                if name.endswith("/"):
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(info) as s, open(target, "wb") as d:
                    shutil.copyfileobj(s, d, 1 << 20)
    else:
        run(["7z", "x", "-y", f"-o{tmp}", archive], stdout=subprocess.DEVNULL)
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(tmp, dest)
    log(f"extracted to {dest}")
    return dest


def find_game_dir(root, quiet=False):
    for d, dirs, files in os.walk(root):
        pcks = [f for f in files if f.endswith(".pck")]
        datas = [x for x in dirs if re.match(r"data_.+_windows_x86_64$", x)]
        if pcks and datas:
            return d
    if not quiet:
        raise RuntimeError(f"no Godot Windows build (.pck + data_*_windows_x86_64) under {root}")
    return None


# ---------------------------------------------------------------- preflight

def is_managed(path):
    try:
        b = open(path, "rb").read(4096)
        pe = struct.unpack_from("<I", b, 0x3C)[0]
        magic = struct.unpack_from("<H", b, pe + 24)[0]
        dd = pe + 24 + (112 if magic == 0x20B else 96)
        return struct.unpack_from("<I", b, dd + 14 * 8)[0] != 0
    except Exception:
        return False


def preflight(game_dir):
    info = {"game_dir": game_dir}
    data = glob.glob(f"{game_dir}/data_*_windows_x86_64")[0]
    info["asm"] = re.match(r"data_(.+)_windows_x86_64$", os.path.basename(data)).group(1)
    info["data_dir"] = data
    dll = f"{data}/{info['asm']}.dll"
    if not os.path.exists(dll):
        raise RuntimeError(f"{info['asm']}.dll missing (NativeAOT build?) – cannot port this way")
    info["dll"] = dll

    exes = glob.glob(f"{game_dir}/*.exe")
    pcks = glob.glob(f"{game_dir}/*.pck")
    pck = next((p for p in pcks if any(os.path.splitext(p)[0] == os.path.splitext(e)[0] for e in exes)), pcks[0])
    info["pck"] = pck
    exe = os.path.splitext(pck)[0] + ".exe"
    m = re.search(rb"Godot Engine v(\d+\.\d+(?:\.\d+)?)\.(stable)\.mono\.official", open(exe, "rb").read())
    if not m:
        raise RuntimeError("could not find an official stable mono Godot version string in the exe")
    info["godot"] = m.group(1).decode()

    hdr = open(pck, "rb").read(24)
    if hdr[:4] != b"GDPC":
        raise RuntimeError("pck magic mismatch")
    flags = struct.unpack_from("<I", hdr, 20)[0]
    if flags & 1:
        raise RuntimeError("pck directory is encrypted – cannot port without the key")

    listing = run([GDRE, "--headless", f"--list-files={pck}"], capture_output=True, text=True).stdout
    native = [l for l in listing.splitlines() if re.search(r"\.(gdextension|so|dylib|dll|framework)$", l.strip())]
    if native:
        raise RuntimeError(f"pck contains native extensions (need macOS builds of them): {native[:5]}")
    if "res://project.binary" not in listing:
        raise RuntimeError("project.binary not found in pck")

    rc = json.load(open(f"{data}/{info['asm']}.runtimeconfig.json"))["runtimeOptions"]
    info["tfm"] = rc["tfm"]
    deps = json.load(open(f"{data}/{info['asm']}.deps.json"))
    tgt = next(v for k, v in deps["targets"].items() if k.endswith("/win-x64"))
    proj = next(v for k, v in tgt.items() if k.startswith(info["asm"] + "/"))
    skip = re.compile(r"^(runtimepack\.|Godot\.|GodotSharp$|Microsoft\.NET\.ILLink\.Tasks$)")
    info["packages"] = {k: v for k, v in proj.get("dependencies", {}).items() if not skip.match(k)}
    info["managed_dlls"] = sorted(os.path.basename(p) for p in glob.glob(f"{data}/*.dll") if is_managed(p))

    meta = f"{TMP}/pck_meta"
    shutil.rmtree(meta, ignore_errors=True)
    run([GDRE, "--headless", f"--extract={pck}", f"--output={meta}", "--include=res://project.binary",
         "--include=res://icon.png"], capture_output=True)
    run([GDRE, "--headless", f"--bin-to-txt={meta}/project.binary"], capture_output=True, cwd=meta)  # must run inside the dir
    pg = open(f"{meta}/project.godot", encoding="utf-8").read()
    info["app_name"] = re.search(r'^config/name="(.*)"', pg, re.M).group(1)
    vm = re.search(r'^config/version="(.*)"', pg, re.M)
    info["app_version"] = vm.group(1) if vm else "0"
    feats = re.search(r"^config/features=PackedStringArray\((.*)\)", pg, re.M)
    info["features"] = feats.group(1) if feats else f'"{info["godot"]}", "C#", "Forward Plus"'
    asm_set = re.search(r'^project/assembly_name="(.*)"', pg, re.M)
    if asm_set and asm_set.group(1) != info["asm"]:
        raise RuntimeError("assembly_name mismatch between project settings and data dir")
    info["icon"] = f"{meta}/icon.png" if os.path.exists(f"{meta}/icon.png") else None
    log(f"preflight: Godot {info['godot']} mono, {info['tfm']}, assembly {info['asm']}, "
        f"app '{info['app_name']}' {info['app_version']}, packages {info['packages']}")
    return info


# ---------------------------------------------------------------- toolchain

def gh_download(tag, name, dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    base = f"https://github.com/godotengine/godot/releases/download/{tag}"
    sums = urllib.request.urlopen(f"{base}/SHA512-SUMS.txt", timeout=60).read().decode()
    want = next(l.split()[0] for l in sums.splitlines() if l.strip().endswith(name))
    path = f"{dest_dir}/{name}"
    if not (os.path.exists(path) and hashlib.sha512(open(path, "rb").read()).hexdigest() == want):
        run(["curl", "-sSL", "--fail", "-o", path, f"{base}/{name}"])
        if hashlib.sha512(open(path, "rb").read()).hexdigest() != want:
            raise RuntimeError(f"SHA512 mismatch for {name}")
    return path


def ensure_godot(ver):
    tag = f"{ver}-stable"
    editor = glob.glob(f"{TOOLS}/godot*/Godot_v{ver}-stable_mono_linux_x86_64/Godot_v{ver}-stable_mono_linux.x86_64")
    if not editor:
        d = f"{TOOLS}/godot-{ver}"
        z = gh_download(tag, f"Godot_v{ver}-stable_mono_linux_x86_64.zip", d)
        run(["unzip", "-q", "-o", z, "-d", d])
        editor = glob.glob(f"{d}/Godot_v{ver}-stable_mono_linux_x86_64/Godot_v{ver}-stable_mono_linux.x86_64")
        log(f"installed Godot {ver} mono editor")
    tdir = f"{TEMPLATES_ROOT}/{ver}.stable.mono"
    need = ["version.txt", "macos.zip", "linux_release.x86_64"]
    if not all(os.path.exists(f"{tdir}/{n}") for n in need):
        d = glob.glob(f"{TOOLS}/godot*/Godot_v{ver}-stable_mono_export_templates.tpz")
        tpz = d[0] if d else gh_download(tag, f"Godot_v{ver}-stable_mono_export_templates.tpz", f"{TOOLS}/godot-{ver}")
        os.makedirs(tdir, exist_ok=True)
        run(["unzip", "-q", "-o", "-j", tpz] + [f"templates/{n}" for n in need] + ["-d", tdir])
        log(f"installed Godot {ver} mono export templates (macOS + Linux)")
    return editor[0], tdir


def ensure_dotnet(tfm):
    major = int(re.match(r"net(\d+)", tfm).group(1))
    sdks = run([f"{DOTNET_DIR}/dotnet", "--list-sdks"], capture_output=True, text=True).stdout
    if not any(int(l.split(".")[0]) >= major for l in sdks.splitlines() if l.strip()):
        run(["bash", f"{TOOLS}/dotnet-install.sh", "--channel", f"{major}.0", "--install-dir", DOTNET_DIR],
            stdout=subprocess.DEVNULL)
        log(f"installed .NET {major} SDK")


# ---------------------------------------------------------------- shell project + exports

def make_shell(info, sdir):
    shutil.rmtree(sdir, ignore_errors=True)
    os.makedirs(sdir)
    sdk = info["godot"] if info["godot"].count(".") == 2 else info["godot"] + ".0"
    pkgs = "\n".join(f'    <PackageReference Include="{k}" Version="{v}" />' for k, v in sorted(info["packages"].items()))
    open(f"{sdir}/{info['asm']}.csproj", "w").write(f"""<Project Sdk="Godot.NET.Sdk/{sdk}">
  <PropertyGroup>
    <TargetFramework>{info['tfm']}</TargetFramework>
    <EnableDynamicLoading>true</EnableDynamicLoading>
    <AssemblyName>{info['asm']}</AssemblyName>
    <RootNamespace>{info['asm']}</RootNamespace>
    <AllowUnsafeBlocks>true</AllowUnsafeBlocks>
  </PropertyGroup>
  <!-- generated from the original {info['asm']}.deps.json -->
  <ItemGroup>
{pkgs}
  </ItemGroup>
</Project>
""")
    open(f"{sdir}/Placeholder.cs", "w").write(
        f"// Placeholder; replaced by the original {info['asm']}.dll after export.\n"
        f"namespace {info['asm']};\ninternal static class Placeholder {{ }}\n")
    icon_line = ""
    if info["icon"]:
        shutil.copy(info["icon"], f"{sdir}/icon.png")
        icon_line = 'application/icon="res://icon.png"\n'
    open(f"{sdir}/project.godot", "w", encoding="utf-8").write(f"""config_version=5

[application]

config/name="{info['app_name']}"
config/version="{info['app_version']}"
config/features=PackedStringArray({info['features']})

[dotnet]

project/assembly_name="{info['asm']}"

[rendering]

textures/vram_compression/import_etc2_astc=true
""")
    short = ".".join(info["app_version"].split(".")[:3])
    common = """dedicated_server=false
custom_features=""
export_filter="all_resources"
include_filter=""
exclude_filter=""
encryption_include_filters=""
encryption_exclude_filters=""
encrypt_pck=false
encrypt_directory=false
"""
    dn = "dotnet/include_scripts_content=false\ndotnet/include_debug_symbols=false\ndotnet/embed_build_outputs=false\n"
    open(f"{sdir}/export_presets.cfg", "w", encoding="utf-8").write(f"""[preset.0]

name="macOS"
platform="macOS"
runnable=true
{common}export_path="mac.zip"

[preset.0.options]

binary_format/architecture="universal"
{icon_line}application/bundle_identifier="{BUNDLE_ID}"
application/app_category="Games"
application/min_macos_version_arm64="{MIN_MACOS}"
application/min_macos_version_x86_64="{MIN_MACOS}"
application/short_version="{short}"
application/version="{info['app_version']}"
display/high_res=true
codesign/codesign=1
notarization/notarization=0
{dn}
[preset.1]

name="Linux"
platform="Linux"
runnable=true
{common}export_path="linux/game.x86_64"

[preset.1.options]

binary_format/embed_pck=false
binary_format/architecture="x86_64"
{dn}""")
    dotnet = f"{DOTNET_DIR}/dotnet"
    run([dotnet, "new", "sln", "-n", info["asm"], "--format", "sln", "-o", sdir], stdout=subprocess.DEVNULL)
    run([dotnet, "sln", f"{sdir}/{info['asm']}.sln", "add", f"{sdir}/{info['asm']}.csproj"], stdout=subprocess.DEVNULL)
    sln = open(f"{sdir}/{info['asm']}.sln").read()
    guid = re.search(rf'"{re.escape(info["asm"])}.csproj", "(\{{[^}}]+\}})"', sln).group(1)
    sln = sln.replace("Debug|Any CPU = Debug|Any CPU", "Debug|Any CPU = Debug|Any CPU\n\t\tExportDebug|Any CPU = "
                      "ExportDebug|Any CPU\n\t\tExportRelease|Any CPU = ExportRelease|Any CPU", 1)
    extra = "".join(f"\t\t{guid}.{c}|Any CPU.ActiveCfg = {c}|Any CPU\n\t\t{guid}.{c}|Any CPU.Build.0 = {c}|Any CPU\n"
                    for c in ("ExportDebug", "ExportRelease"))
    sln = sln.replace(f"\t\t{guid}.Debug|Any CPU.ActiveCfg", extra + f"\t\t{guid}.Debug|Any CPU.ActiveCfg", 1)
    open(f"{sdir}/{info['asm']}.sln", "w").write(sln)


def export(godot, sdir, preset, out_path, logf):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(logf, "w") as lf:
        run([godot, "--headless", "--path", sdir, "--export-release", preset, out_path], stdout=lf, stderr=lf)
    errs = [l for l in open(logf, errors="replace") if l.startswith("ERROR")]
    if errs:
        raise RuntimeError(f"{preset} export errors: {errs[:5]}")


# ---------------------------------------------------------------- tests

def linux_smoke_test(info, build_dir, patched_dll, shots_dir):
    test = f"{build_dir}/linux_test"
    shutil.rmtree(test, ignore_errors=True)
    shutil.copytree(f"{build_dir}/linux", test)
    shutil.copy(info["pck"], f"{test}/game.pck")
    shutil.copy(patched_dll, glob.glob(f"{test}/data_*_linuxbsd_x86_64")[0] + f"/{info['asm']}.dll")
    disp = next(n for n in range(90, 120) if not os.path.exists(f"/tmp/.X{n}-lock"))
    xv = subprocess.Popen(["Xvfb", f":{disp}", "-screen", "0", "1920x1080x24"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    env = dict(ENV, DISPLAY=f":{disp}")
    logf = f"{test}/run.log"
    try:
        with open(logf, "w") as lf:
            g = subprocess.Popen([f"{test}/game.x86_64", "--verbose"], cwd=test, env=env, stdout=lf, stderr=lf)
            alive = True
            for _ in range(90):
                time.sleep(1)
                if g.poll() is not None:
                    alive = False
                    break
            os.makedirs(shots_dir, exist_ok=True)
            shot = f"{shots_dir}/linux_title.png"
            subprocess.run(["import", "-display", f":{disp}", "-window", "root", "-resize", "50%", shot], env=env)
            g.terminate()
            try:
                g.wait(15)
            except subprocess.TimeoutExpired:
                g.kill()
    finally:
        xv.terminate()
    text = open(logf, errors="replace").read()
    ok_init = ".NET: GodotPlugins initialized" in text
    bad = [l for l in text.splitlines() if re.match(r"(SCRIPT ERROR|USER ERROR)|.*Unhandled exception|.*Failed to get GodotPlugins", l)]
    result = {"alive_90s": alive, "dotnet_init": ok_init, "script_errors": bad[:10]}
    log(f"linux smoke test: {result}")
    if not (alive and ok_init and not bad):
        raise RuntimeError(f"Linux smoke test failed: {result} (log: {logf})")
    return result


def arm64_load_test(info, patched_dll, mac_arm64_dir):
    lt = f"{WORK}/loadtest"
    out = f"{lt}/out_{info['tfm']}"
    run([f"{DOTNET_DIR}/dotnet", "build", lt, "-c", "Release", f"-p:TargetFramework={info['tfm']}", "-o", out],
        stdout=subprocess.DEVNULL)
    t = f"{TMP}/arm64test"
    shutil.rmtree(t, ignore_errors=True)
    os.makedirs(f"{t}/deps")
    for f in glob.glob(f"{out}/loadtest.*"):
        shutil.copy(f, t)
    shutil.copy(patched_dll, f"{t}/game.dll")
    for f in glob.glob(f"{mac_arm64_dir}/*.dll"):
        shutil.copy(f, f"{t}/deps/")
    major = re.match(r"net(\d+)", info["tfm"]).group(1)
    r = run(["docker", "run", "--rm", "--platform", "linux/arm64", "-v", f"{t}:/t", "-w", "/t",
             f"mcr.microsoft.com/dotnet/runtime:{major}.0", "sh", "-c", "uname -m; dotnet loadtest.dll game.dll /t/deps"],
            capture_output=True, text=True, check=False)
    outp = r.stdout + r.stderr
    log("arm64 load test: " + " | ".join(l for l in outp.splitlines() if l.startswith(("aarch64", "LOAD", "types"))))
    if "aarch64" not in outp or "LOAD OK" not in outp or "failed: 0" not in outp:
        raise RuntimeError(f"arm64 load test failed:\n{outp[-2000:]}")


def text_section(macho, arch, outp):
    run(["llvm-lipo-21", "-thin", arch, macho, "-output", outp + ".bin"])
    run(["llvm-objcopy-21", f"--dump-section=__TEXT,__text={outp}", outp + ".bin", "/dev/null"])
    return open(outp, "rb").read()


# ---------------------------------------------------------------- main build

def build(info, version, publish):
    godot, tdir = ensure_godot(info["godot"])
    ensure_dotnet(info["tfm"])
    bdir = f"{WORK}/builds/{version}"
    sdir = f"{bdir}/shell"
    shutil.rmtree(bdir, ignore_errors=True)
    os.makedirs(bdir)
    make_shell(info, sdir)
    with open(f"{bdir}/import.log", "w") as lf:
        run([godot, "--headless", "--path", sdir, "--import"], stdout=lf, stderr=lf, check=False)
    export(godot, sdir, "Linux", f"{bdir}/linux/game.x86_64", f"{bdir}/export_linux.log")
    export(godot, sdir, "macOS", f"{bdir}/mac.zip", f"{bdir}/export_mac.log")
    log("exported Linux + macOS shells")

    patched = f"{bdir}/{info['asm']}.dll"
    run(["python3", "-I", f"{WORK}/pe_anycpu.py", info["dll"], patched], stdout=subprocess.DEVNULL)
    if open(info["dll"], "rb").read()[512:] != open(patched, "rb").read()[512:]:
        raise RuntimeError("PE patch touched bytes beyond the header")

    stage = f"{bdir}/stage"
    run(["unzip", "-q", f"{bdir}/mac.zip", "-d", stage])
    app = glob.glob(f"{stage}/*.app")[0]
    res = f"{app}/Contents/Resources"
    exe = glob.glob(f"{app}/Contents/MacOS/*")[0]
    shutil.copy(info["pck"], glob.glob(f"{res}/*.pck")[0])
    data_dirs = glob.glob(f"{res}/data_{info['asm']}_macos_*")
    for d in data_dirs:
        shutil.copy(patched, f"{d}/{info['asm']}.dll")
    arm = next(d for d in data_dirs if d.endswith("_arm64"))
    missing = [m for m in info["managed_dlls"] if not os.path.exists(f"{arm}/{m}")]
    if missing:
        raise RuntimeError(f"managed assemblies of the original missing in macOS build: {missing}")

    tpl = f"{bdir}/tpl"
    run(["unzip", "-q", "-o", "-j", f"{tdir}/macos.zip", "macos_template.app/Contents/MacOS/godot_macos_release.universal",
         "-d", tpl])
    for arch in ("arm64", "x86_64"):
        if text_section(exe, arch, f"{tpl}/new_{arch}") != text_section(
                f"{tpl}/godot_macos_release.universal", arch, f"{tpl}/tpl_{arch}"):
            raise RuntimeError(f"macOS executable {arch} code differs from the official template (truncated export?)")
    shutil.rmtree(tpl)
    log("macOS executable verified against official template (arm64 + x86_64)")

    shots = f"{bdir}/screenshots"
    smoke = linux_smoke_test(info, bdir, patched, shots)
    arm64_load_test(info, patched, arm)

    shutil.rmtree(f"{app}/Contents/_CodeSignature", ignore_errors=True)
    with open(f"{bdir}/sign.log", "w") as lf:
        run([RCODESIGN, "sign", app], stdout=lf, stderr=lf)
    sig = run([RCODESIGN, "print-signature-info", exe], capture_output=True, text=True).stdout
    if sig.count("allow-jit") < 2 or "ADHOC" not in sig:
        raise RuntimeError("signature/entitlements check failed")

    pkg = f"{bdir}/{PKG_NAME}"
    run(["tar", "--owner=0", "--group=0", "--numeric-owner", "--mode=u+rwX,go+rX,go-w",
         "-C", stage, "-czf", pkg, os.path.basename(app)])
    sha = hashlib.sha256(open(pkg, "rb").read()).hexdigest()
    log(f"packaged {pkg} sha256={sha}")
    shutil.rmtree(f"{bdir}/linux_test", ignore_errors=True)
    shutil.rmtree(stage, ignore_errors=True)

    if publish:
        os.makedirs(OUT, exist_ok=True)
        shutil.copy(pkg, f"{OUT}/{PKG_NAME}.new")
        os.replace(f"{OUT}/{PKG_NAME}.new", f"{OUT}/{PKG_NAME}")
        open(f"{OUT}/{PKG_NAME}.sha256", "w").write(f"{sha}  {PKG_NAME}\n")
        open(f"{OUT}/VERSION.txt", "w").write(f"{version}\n")
        mi = f"{OUT}/MAC_INSTALL.md"
        if os.path.exists(mi):
            s = open(mi, encoding="utf-8").read()
            s = re.sub(r"(上面输出的值应为 `)[0-9a-f]{64}(`)", rf"\g<1>{sha}\g<2>", s)
            s = re.sub(r"^# 植物大战僵尸杂交版 [0-9.]+ ", f"# 植物大战僵尸杂交版 {version} ", s, flags=re.M)
            open(mi, "w", encoding="utf-8").write(s)
        if os.path.exists(f"{shots}/linux_title.png"):
            os.makedirs(f"{OUT}/screenshots_linux_test", exist_ok=True)
            shutil.copy(f"{shots}/linux_title.png", f"{OUT}/screenshots_linux_test/latest_title.png")
        log(f"published {OUT}/{PKG_NAME} ({version})")
        for old in glob.glob(f"{WORK}/builds/*"):           # keep only the build that was just published
            if os.path.realpath(old) != os.path.realpath(bdir):
                shutil.rmtree(old, ignore_errors=True)
    return {"sha256": sha, "package": pkg, "smoke": smoke}


def append_log(line):
    p = f"{OUT}/UPDATE_LOG.md"
    new = not os.path.exists(p)
    with open(p, "a", encoding="utf-8") as f:
        if new:
            f.write("# 自动更新记录\n\n| 时间 | 版本 | 结果 |\n|---|---|---|\n")
        f.write(line + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--game-dir")
    ap.add_argument("--no-publish", action="store_true")
    a = ap.parse_args()
    os.makedirs(TMP, exist_ok=True)
    lockf = open(f"{AU}/.lock", "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another run is in progress; exiting")
        return 0
    st = load_state()
    now = f"{datetime.datetime.now():%Y-%m-%d %H:%M}"
    version = None
    try:
        if a.game_dir:
            game_dir = find_game_dir(a.game_dir)
            info = preflight(game_dir)
            version = info["app_version"]
        else:
            version, post, sid, pc, stoken, arc = acquire_latest()
            st.update(last_check=now, latest_seen=version)
            if not a.force and st.get("built_version") == version:
                log(f"up to date ({version}); nothing to do")
                save_state(st)
                return 0
            if not a.force and st.get("built_version") and vtuple(version) < vtuple(st["built_version"]):
                log(f"remote version {version} is older than built {st['built_version']}; skipping")
                save_state(st)
                return 0
            archive = ensure_in_drive(sid, stoken, arc)
            game_dir = find_game_dir(extract(archive))
            info = preflight(game_dir)
        res = build(info, version, publish=not a.no_publish)
        if not a.no_publish:
            st.update(built_version=version, built_at=now, sha256=res["sha256"], game_dir=game_dir, last_error=None)
            append_log(f"| {now} | {version} | ✅ 已构建并发布，sha256 `{res['sha256'][:16]}…` |")
        save_state(st)
        return 0
    except Exception as e:
        log(f"FAILED: {type(e).__name__}: {e}")
        st.update(last_error=f"{now} {type(e).__name__}: {e}")
        save_state(st)
        if not a.no_publish:
            append_log(f"| {now} | {version or '?'} | ❌ 失败：{type(e).__name__}: {str(e)[:200]}（Mac 包未更新） |")
        return 1


if __name__ == "__main__":
    sys.exit(main())
