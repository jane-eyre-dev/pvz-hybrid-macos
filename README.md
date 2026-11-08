# 植物大战僵尸杂交版 — macOS 原生移植（非官方）

[English](#english) | 中文

> ⚠️ **这是玩家自制的非官方 Mac 移植，与原作者无关。**
> 原作：潜艇伟伟迷（B 站），官网 <https://www.pvzhe.com>。本移植免费分享，请勿付费购买；Mac 版的问题请不要去找原作者。
> 《植物大战僵尸》是 PopCap / EA 的商标。

当前版本：**0.32.0.0**。下载在 [Releases](../../releases/latest) 页面。原作者发布新版后，这里每天自动检查、构建并发布对应的 Mac 版。

- 支持 Apple Silicon（M1/M2/M3/M4…）和 Intel Mac，Universal（arm64 + x86_64），需要 macOS 12 (Monterey) 或更高版本（内置 .NET 9 runtime 的要求）
- 原生运行，不需要 Wine、CrossOver、虚拟机或 Rosetta

## 安装

1. 从 [Releases](../../releases) 下载 `PvZ-Hybrid-0.32-macOS-Universal.tar.gz`，双击解压，得到「植物大战僵尸杂交版」应用。
2. 把它拖进「应用程序」文件夹。
3. 第一次打开前，要解除 macOS 的"来自网络"隔离。这个版本没有 Apple 开发者签名，不处理的话系统会提示"已损坏"或"无法验证开发者"。
   打开「终端」，执行：

   ```bash
   xattr -cr /Applications/植物大战僵尸杂交版.app
   ```

4. 双击打开游戏。

如果还是打不开，在本机重新做一次 ad-hoc 签名，然后再打开：

```bash
codesign --force --deep -s - /Applications/植物大战僵尸杂交版.app
```

仍然不行的话，在终端里直接运行，可以看到报错信息（欢迎提 issue 附上输出）：

```bash
/Applications/植物大战僵尸杂交版.app/Contents/MacOS/植物大战僵尸杂交版
```

### 校验文件（可选）

```bash
shasum -a 256 ~/Downloads/PvZ-Hybrid-0.32-macOS-Universal.tar.gz
```

结果应与 Release 页面上的 SHA256 一致。

## 存档、Mod 和设置

存档位置（在访达里按 Shift+Command+G，粘贴路径即可打开）：

```
~/Library/Application Support/Godot/app_userdata/植物大战僵尸杂交版/Csharp/
```

- **迁移 Windows 存档**：把 Windows 上 `%APPDATA%\Godot\app_userdata\植物大战僵尸杂交版\Csharp\` 整个文件夹复制过来，替换上面的 `Csharp` 文件夹（请先退出游戏）。
- **Mod 目录**：`~/Library/Application Support/Godot/app_userdata/植物大战僵尸杂交版/Mods/`
- **清除配置**（相当于 Windows 启动器里的"清除配置"）：

  ```bash
  rm "$HOME/Library/Application Support/Godot/app_userdata/植物大战僵尸杂交版/Csharp/config.res"
  ```

- **画面异常**：用 OpenGL（兼容模式）启动试试：

  ```bash
  /Applications/植物大战僵尸杂交版.app/Contents/MacOS/植物大战僵尸杂交版 --rendering-driver opengl3
  ```

## 常见问题

- 联机建房时 macOS 可能会询问是否允许传入连接，允许即可；不联机可以拒绝。
- 游戏内"发现新版本"会跳到 Windows 版下载页；Mac 版要等这里更新。
- 游戏会访问官方服务器 `api.pvzhe.com`（检查版本、每日挑战、在线关卡等），和 Windows 版一样。
- 更多说明见 [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md)。

## 这个 Mac 版是怎么做的

原版不是 Unity 游戏，而是 **Godot 4.7（mono 官方版）+ C#（.NET 9）**，所以不需要反编译和重写代码：

1. 用官方 Godot 4.7 mono 编辑器导出一个 macOS Universal 的"壳"。壳的程序集名和 NuGet 依赖版本都与原版一致，自带官方 macOS 引擎和 osx-arm64 / osx-x64 的 .NET 9 runtime。
2. 把**原版的** `.pck`（全部资源、场景、shader 源码）原样放进去，与原版逐字节一致；再放入**原版的** `PlantsVsZombies.dll`（游戏全部 C# 逻辑）。
3. 用 [rcodesign](https://github.com/indygreg/apple-platform-rs) 在 Linux 上做 ad-hoc 签名，然后打包。

**唯一的二进制改动**：原版 DLL 是纯 IL，但编译时设成了 x64 平台（PE32+ / AMD64），arm64 上的 .NET 会拒绝加载。
[`pe_anycpu.py`](pe_anycpu.py) 把 PE 头改写为 AnyCPU，只改动文件前 0x200 字节，IL、元数据和资源都不变（构建脚本会校验这一点）。

### 验证情况

首次移植（0.30.0.0）时做了下面这些验证。之后的版本由自动更新构建，每个版本都要通过 Linux 冒烟测试（启动后运行 90 秒，.NET 初始化成功、无脚本错误）、arm64 .NET 加载测试和主程序与官方模板的比对，才会发布。

| 项目 | 结果 |
|---|---|
| Linux x86_64 上用同样方法组装并运行：标题 → 新建用户 → 主菜单 → 冒险 1-1 → 种植，无脚本错误 | ✅ |
| 补丁版 DLL 在 arm64 .NET 9 上加载，11413 个类型全部解析 | ✅ |
| macOS 主程序两个切片的 `__text` 与官方 Godot 4.7 模板逐字节一致 | ✅ |
| 在真实 Mac（Apple Silicon / Intel）上运行 | ⚠️ 尚未确认（首个构建在 Apple Silicon 上启动崩溃，原因已定位并修复） |

## 从源码构建

需要 Linux x86_64，以及一份已解压的 Windows 版游戏。本仓库不包含游戏的资源和代码，只有 `shell/icon.png` 是原版图标，用作 app 图标。

依赖：Godot 4.7 mono 编辑器和导出模板、.NET 9 SDK、[rcodesign](https://github.com/indygreg/apple-platform-rs)、Python 3；`autoupdate/` 另外需要 [GDRE Tools](https://github.com/GDRETools/gdsdecomp)、`llvm-lipo` / `llvm-objcopy`，以及本机的 OpenList 和夸克登录。

- [`build_mac.sh`](build_mac.sh)：手动构建。默认目录结构是 `~/pvzhe/work`（本仓库）、`~/pvzhe/tools`（Godot 4.7 mono、.NET 9 SDK、rcodesign）、`~/pvzhe/out`（输出）。游戏目录用 `GAME_DIR` 环境变量指定。
- [`autoupdate/`](autoupdate/)：全自动更新。检查新版本 → 下载 → 预检 → 构建 → Linux 冒烟测试 + arm64 加载测试 → 签名打包 → 发布 GitHub Release。说明见 [docs/AUTO_UPDATE.md](docs/AUTO_UPDATE.md)。
- [`shell/`](shell/)：Godot 壳项目。[`loadtest/`](loadtest/)：arm64 加载测试程序。
- 移植过程记录：[docs/PROGRESS.md](docs/PROGRESS.md)。

---

## English

> ⚠️ **Unofficial, fan-made macOS port. Not affiliated with the original author.**
> *Plants vs. Zombies Hybrid* (植物大战僵尸杂交版) is made by 潜艇伟伟迷 (bilibili), <https://www.pvzhe.com>. This port is free; do not pay for it, and please don't report Mac issues to the original author.
> *Plants vs. Zombies* is a trademark of PopCap / EA.

A native macOS build of PvZ Hybrid **0.32.0.0**: Universal (Apple Silicon + Intel), macOS 12 (Monterey) or later, no Wine, VM or Rosetta. Download from [Releases](../../releases/latest); new upstream versions are checked, built and released here automatically every day.

### Install

1. Download `PvZ-Hybrid-0.32-macOS-Universal.tar.gz`, double-click to extract, and move `植物大战僵尸杂交版.app` to `/Applications`.
2. The app is ad-hoc signed but not notarized, so remove the quarantine flag before the first launch:

   ```bash
   xattr -cr /Applications/植物大战僵尸杂交版.app
   ```

3. Open it. If it still won't launch, run `codesign --force --deep -s - /Applications/植物大战僵尸杂交版.app` and try again, or run the binary in `Contents/MacOS/` from Terminal to see the error.

Saves: `~/Library/Application Support/Godot/app_userdata/植物大战僵尸杂交版/Csharp/` (copy your Windows `%APPDATA%\Godot\app_userdata\植物大战僵尸杂交版\Csharp\` here to migrate). Graphics glitches: launch with `--rendering-driver opengl3`. Mods: `~/Library/Application Support/Godot/app_userdata/植物大战僵尸杂交版/Mods/`.

Notes: macOS may ask to allow incoming connections when hosting online games. The in-game "new version" prompt points to the Windows download; Mac builds are published here. The game talks to the official server `api.pvzhe.com` (version check, daily challenges, online levels), same as on Windows. More in [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md) (Chinese).

### How it works

The game is **Godot 4.7 mono + C# (.NET 9)**, not Unity. The build exports an official Godot 4.7 mono macOS Universal shell with the same assembly name and NuGet dependencies, then drops in the **original** `.pck` (byte-identical) and the **original** `PlantsVsZombies.dll`, re-signs ad hoc and packages it. No game code is recompiled (the DLL was decompiled with ilspycmd only to review it; that output is not used in the build or included here).

The only binary change: the original DLL is IL-only but marked x64 (PE32+/AMD64), which CoreCLR refuses to load on arm64. [`pe_anycpu.py`](pe_anycpu.py) rewrites just the PE header (first 0x200 bytes) to AnyCPU; the build verifies everything after the header is unchanged.

Status: the first port (0.30.0.0) was tested on Linux x86_64 (same assembly method; title → new user → main menu → Adventure 1-1 → planting, no script errors) and on arm64 .NET 9 (DLL loads, all 11413 types resolve). Later versions are built automatically and are only released after passing a Linux smoke test (90 s run, .NET initialized, no script errors), an arm64 .NET load test and a check of the main executable against the official template. **No build has been confirmed on a real Mac (Apple Silicon or Intel) yet.**

### Build from source

Requires Linux x86_64 and an extracted Windows copy of the game. This repo contains no game assets or code, except `shell/icon.png` (the original game icon, used as the app icon). Tools: Godot 4.7 mono + export templates, .NET 9 SDK, rcodesign, Python 3; `autoupdate/` also needs GDRE Tools, `llvm-lipo` / `llvm-objcopy`, and a local OpenList with a Quark login. See [`build_mac.sh`](build_mac.sh) for a manual build and [`autoupdate/`](autoupdate/) for the automated pipeline ([docs/AUTO_UPDATE.md](docs/AUTO_UPDATE.md), Chinese).
