# 自动更新

[`autoupdate/autoupdate.py`](../autoupdate/autoupdate.py) 每天由 cron 运行一次，检查杂交版 PC 版有没有新版本。
有新版本时，会自动构建新的 Mac 包；全部测试通过后才发布。

## 流程

1. 请求游戏官方的版本接口 `api.pvzhe.com/new_version`（和游戏启动时发出的请求相同）。
2. 版本比上次构建的新 → 打开接口返回的作者 B 站公告，取出「电脑（Windows）版」的夸克分享链接。
3. 把分享转存到维护者自己的夸克网盘（使用本机 [OpenList](https://github.com/OpenListTeam/OpenList) 里保存的夸克登录状态），
   再通过 OpenList 下载到 `~/quark-downloads/`，然后解压。
4. 预检：Godot 版本、.NET 版本、pck 是否加密、有没有原生插件、依赖包列表。
   新版换了引擎版本或 .NET 版本时，会从官方（GitHub / 微软）下载对应的工具，并校验 SHA512。
5. 生成壳项目 → 导出 macOS + Linux → 把 macOS 主程序与官方模板逐字节比对。
6. 测试，**全部通过才会发布**：
   - Linux 冒烟测试：启动后运行 90 秒，要求 .NET 初始化成功，且没有脚本错误；
   - arm64 加载测试：在 arm64 的 .NET 上加载游戏 DLL，并解析全部类型。
7. 签名、打包，发布到 `~/pvzhe/out/`，写入 `UPDATE_LOG.md`。

任何一步失败，都不会替换现有的 Mac 包，失败原因会写进 `UPDATE_LOG.md`。

## 会自动失败、需要人工处理的情况

- 作者给 pck 加了密，或者用了需要原生库的插件（GDExtension）。
- 改用了 NativeAOT（没有 `PlantsVsZombies.dll`）。
- 不再是 Godot 官方的 stable mono 版本。
- B 站公告的格式大改，找不到 Windows 版链接。
- 夸克登录过期（需要在 OpenList 里重新填写 Cookie）。

## 用法

```bash
cd ~/pvzhe/work/autoupdate && python3 -I autoupdate.py            # 检查并按需构建
cd ~/pvzhe/work/autoupdate && python3 -I autoupdate.py --force    # 强制重新构建当前版本
cd ~/pvzhe/work/autoupdate && python3 -I autoupdate.py --game-dir DIR --no-publish   # 用本地游戏目录构建，不发布
```

cron 示例（每天 12:00；需要指定时区的话，在 crontab 开头加 `CRON_TZ=<时区>`）：

```
0 12 * * * cd $HOME/pvzhe/work/autoupdate && mkdir -p logs && LANG=C.UTF-8 /usr/bin/python3 -I autoupdate.py >> "logs/$(date +\%Y-\%m).log" 2>&1
```

密钥（夸克 Cookie、OpenList token）在运行时从本机 OpenList 读取，不会写进仓库或日志。
