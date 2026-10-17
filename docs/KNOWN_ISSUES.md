# 已知问题与说明

## 可能和 Windows 版不一样的地方

- **压缩纹理**：pck 里有 111 张 S3TC/BPTC 格式的压缩纹理（其余 3700 多张是无损纹理）。Apple Silicon 的 Metal 支持这些格式，
  按理不会有问题；如果个别贴图显示异常，请用 `--rendering-driver opengl3` 试试，并提 issue 说明是哪里。
- **快捷键**：游戏里的按键绑定是按物理键位定义的（如 `` ` `` 打开控制台、空格暂停、1/2/3 等），在 Mac 键盘上位置相同；
  没有针对 Cmd 键做特殊适配。
- **Windows 启动器**（`杂交版启动补丁.cs.hta`）在 Mac 上用不了。它的功能（清除配置、打开存档位置、切换渲染器）
  在 [README](../README.md) 里都有对应的终端命令。
- **Mod 编辑器里的脚本编译功能**会查找 `dotnet` 工具链；Mac 上如果没装 .NET SDK，这个功能可能不可用（不影响正常游戏）。
- **未做 Apple 公证**：首次打开前需要执行 `xattr -cr`（见 README）。
- **包体积**：原版启用了 .NET 裁剪（ILLink），Mac 版带的是完整 runtime，并且同时包含 arm64 和 x86_64 两套，
  所以解压后约 880 MB，比 Windows 版大。

## DLL 的 PE 头补丁

原版 `PlantsVsZombies.dll` 是纯 IL（ILONLY），但作者编译时设置了 x64 平台（PE32+ / Machine=AMD64）。
.NET 在 Apple Silicon（arm64）上会拒绝加载这种程序集，报 `The assembly architecture is not compatible with the
current process architecture`，导致 C# 层初始化失败，引擎随后在启动时崩溃（`.NET: Failed to get GodotPlugins initialization function pointer`）。

修复：[`pe_anycpu.py`](../pe_anycpu.py) 把 PE 头改写为 AnyCPU（PE32 / Machine=I386 / ILONLY），
**只改动文件前 0x200 字节的头部，IL、元数据、资源完全不变**（构建脚本会校验这一点）。

在 arm64 的 .NET 9 上做了验证（docker `mcr.microsoft.com/dotnet/runtime:9.0`，linux/arm64）：原版 DLL 加载失败，报错与 Mac 上一致；
补丁版加载成功，入口 `GodotPlugins.Game.Main.InitializeFromGameProject` 能找到，11413 个类型全部解析成功。

## 验证情况

| 项目 | 结果 |
|---|---|
| Linux x86_64 上用同样方法组装（壳 + 原版 pck + 原版 dll），软件 Vulkan / Forward+ 运行 | ✅ 通过 |
| 启动 → 加载 607 个角色场景、720 张卡片（`packetBankMissingCount=0`） | ✅ 无脚本错误、无异常 |
| 标题画面 → 新建用户（文字输入）→ 主菜单 → 冒险模式 → 关卡 1-1 → 对话 → 种植豌豆射手、阳光扣除、僵尸行进 | ✅ 正常 |
| macOS 包结构：Universal 主程序、arm64/x86_64 两套 .NET runtime、签名覆盖 30 个嵌套 Mach-O | ✅ 静态检查通过 |
| 补丁版 DLL：arm64 .NET 9 加载 + 全部类型解析 | ✅ 通过 |
| 补丁版 DLL：Linux 回归（启动 → 主菜单 → 读取已有存档 → 游戏内"退出"，退出码 0） | ✅ 通过 |
| macOS 主程序两个切片的 `__text` 与官方 Godot 4.7 模板逐字节一致 | ✅ |
| **在真实 Mac（Apple Silicon / Intel）上运行当前构建** | ⚠️ 尚未确认 |

Linux 版和 Mac 版唯一的区别是引擎二进制和 .NET runtime，两者都是官方的 macOS 版本；游戏数据和逻辑完全相同。
但在真实 Mac 上确认之前，不能保证 100% 没有问题。
