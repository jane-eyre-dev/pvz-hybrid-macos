#!/usr/bin/env bash
# Build a native macOS (Universal: arm64 + x86_64) app of PvZ Hybrid 0.30 (Godot 4.7 mono).
# Method: export an official Godot 4.7 mono macOS shell with the same assembly name and NuGet
# dependencies, then drop in the ORIGINAL game pack (.pck) and ORIGINAL game IL (PlantsVsZombies.dll),
# re-sign ad-hoc and package as tar.gz (keeps executable bits).
set -euo pipefail

GAME_DIR="${GAME_DIR:-$HOME/quark-downloads/unzipped/植物大战僵尸杂交版0.30/植物大战僵尸杂交重制版}"
ORIG_PCK="$GAME_DIR/植物大战僵尸杂交版发布版0.30.0.Csharp.pck"
ORIG_DLL="$GAME_DIR/data_PlantsVsZombies_windows_x86_64/PlantsVsZombies.dll"
WORK="$HOME/pvzhe/work"
OUT="$HOME/pvzhe/out"
GODOT="$HOME/pvzhe/tools/godot/Godot_v4.7-stable_mono_linux_x86_64/Godot_v4.7-stable_mono_linux.x86_64"
RCODESIGN="$HOME/pvzhe/tools/rcodesign/apple-codesign-0.29.0-x86_64-unknown-linux-musl/rcodesign"
APP_NAME="植物大战僵尸杂交版"
mkdir -p "$WORK/tmp"
export TMPDIR="$WORK/tmp"   # /tmp is a small shared tmpfs; a full /tmp silently truncated the exported binary
export DOTNET_ROOT="$HOME/pvzhe/tools/dotnet" PATH="$HOME/pvzhe/tools/dotnet:$PATH" DOTNET_CLI_TELEMETRY_OPTOUT=1

echo "== 1. export macOS shell"
rm -rf "$WORK/build/mac" && mkdir -p "$WORK/build/mac"
"$GODOT" --headless --path "$WORK/shell" --export-release "macOS" "$WORK/build/mac/PvZHE.zip" > "$WORK/export_mac.log" 2>&1
if grep -qE '^ERROR' "$WORK/export_mac.log"; then grep -E '^ERROR' "$WORK/export_mac.log"; exit 1; fi

echo "== 2. unpack and swap in original game files"
# The original PlantsVsZombies.dll is IL-only but was built with PlatformTarget=x64 (PE32+/AMD64),
# which CoreCLR refuses to load on arm64. Rewrite only its PE header to AnyCPU (PE32/I386); IL untouched.
PATCHED_DLL="$WORK/patched/PlantsVsZombies.dll"
mkdir -p "$WORK/patched"
python3 -I "$WORK/pe_anycpu.py" "$ORIG_DLL" "$PATCHED_DLL"
cmp <(tail -c +513 "$ORIG_DLL") <(tail -c +513 "$PATCHED_DLL")   # everything after the 0x200 header is identical
STAGE="$WORK/stage_mac"
rm -rf "$STAGE" && mkdir -p "$STAGE"
unzip -q "$WORK/build/mac/PvZHE.zip" -d "$STAGE"
APP="$STAGE/$APP_NAME.app"
RES="$APP/Contents/Resources"
cp "$ORIG_PCK" "$RES/$APP_NAME.pck"
for d in "$RES"/data_PlantsVsZombies_macos_*; do
  cp "$PATCHED_DLL" "$d/PlantsVsZombies.dll"
done
cmp "$ORIG_PCK" "$RES/$APP_NAME.pck"
for d in "$RES"/data_PlantsVsZombies_macos_*; do cmp "$PATCHED_DLL" "$d/PlantsVsZombies.dll"; done

echo "== 3. ad-hoc re-sign (no hardened runtime, so .NET JIT works)"
rm -rf "$APP/Contents/_CodeSignature"
"$RCODESIGN" sign "$APP" > "$WORK/sign.log" 2>&1 || { tail -20 "$WORK/sign.log"; exit 1; }
"$RCODESIGN" verify "$APP/Contents/MacOS/$APP_NAME" > "$WORK/verify.log" 2>&1 && echo "signature verify: OK" || { echo "signature verify FAILED"; tail -20 "$WORK/verify.log"; }

echo "== 4. package"
mkdir -p "$OUT"
rm -f "$OUT/PvZ杂交版-mac.tar.gz"
# numeric root ownership and no group/other write bits, so the archive carries no build-host user info
tar --owner=0 --group=0 --numeric-owner --mode=u+rwX,go+rX,go-w -C "$STAGE" -czf "$OUT/PvZ杂交版-mac.tar.gz" "$APP_NAME.app"
ls -la "$OUT"
(cd "$OUT" && sha256sum "PvZ杂交版-mac.tar.gz" | tee "PvZ杂交版-mac.tar.gz.sha256")
