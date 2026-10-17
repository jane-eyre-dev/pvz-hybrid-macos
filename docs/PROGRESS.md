# PvZ 杂交版 0.30 → macOS 移植记录

## 结论
- 引擎：**Godot 4.7.stable.mono.official** + .NET 9（不是 Unity，所以不需要 IL2CPP 那类反编译/重写路线）
- pck：v4 格式，27426 个文件，未加密，无 GDExtension/原生库；11 个 GDScript 字节码；纹理 111 张 S3TC，其余无损
- `PlantsVsZombies.dll`：无 Windows P/Invoke，代码自带 macOS 分支；所有写盘都走 `user://`
- 路线：官方 Godot 4.7 mono 导出同名、同依赖的 macOS Universal 壳 → 换入原版 pck + 原版 dll → rcodesign ad-hoc 签名 → tar.gz

## 步骤
- [x] 工具：.NET 9 SDK、Godot 4.7 mono 编辑器与导出模板（SHA512 校验）、GDRE Tools 2.7.4、rcodesign 0.29.0（SHA256 校验）、ilspycmd
- [x] 分析 pck 与 dll（用 ilspycmd 反编译只是为了审查代码，反编译结果不参与构建，也不在本仓库里）
- [x] `shell/` 壳项目（csproj 依赖与原版 deps.json 一致；GodotSharp/ZLogger/Roslyn 等与原版逐字节相同）
- [x] Linux 验证：标题 → 新建用户 → 主菜单 → 冒险 → 1-1 → 种植，无脚本错误
- [x] macOS 包（`build_mac.sh` 可重复构建）
- [x] 首个 Mac 构建在 Apple Silicon 上启动崩溃（pc=0）。日志：`.NET: Failed to get GodotPlugins initialization function pointer`
      原因：原版 dll 是 x64 平台（PE32+/AMD64，纯 IL），arm64 CoreCLR 拒绝加载（docker arm64 上复现）
      修复：`pe_anycpu.py` 把 PE 头改为 AnyCPU（只改前 0x200 字节）；arm64 加载验证通过；Linux 回归通过
- [x] 另修：`/tmp` 空间不足会导致 Godot 导出的主程序被截断 → 构建时把 `TMPDIR` 设到 `work/tmp`；`autoupdate.py` 构建后会核对 arm64/x86_64 两个切片的 `__text` 与官方模板一致（`build_mac.sh` 不做这项检查）
- [x] 自动更新：`autoupdate/`（版本接口 → B 站公告 → 夸克 → OpenList 下载 → 构建 → 测试 → 发布）
- [x] 发布：GitHub Releases
- [ ] 在真实 Mac（Apple Silicon 和 Intel）上确认修复后的构建
