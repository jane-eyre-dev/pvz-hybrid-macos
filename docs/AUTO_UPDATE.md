# 自动更新

[`autoupdate/autoupdate.py`](../autoupdate/autoupdate.py) 每天由 cron 运行一次，检查杂交版 PC 版有没有新版本。
有新版本时，会自动构建新的 Mac 包；全部测试通过后才发布，并且可以自动发布成本仓库的 GitHub Release。

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
8. 指定了 `--github-repo` 时，再发布到 GitHub：
   - 创建 Release `v<版本号>`，上传 `PvZ-Hybrid-<版本>-macOS-Universal.tar.gz` 和它的 `.sha256`，并核对上传后的文件大小和状态；
   - Release 完整之后，再把 README 里的版本号和下载文件名改成新版本，以 commit 推送到 `main`。

任何一步失败，都不会替换现有的 Mac 包，失败原因会写进 `UPDATE_LOG.md`。

GitHub 发布失败（比如网络问题）不影响本地的 Mac 包；之后每次运行（包括构建失败的那次）都会重试，直到发布成功。同样的失败原因只在 `UPDATE_LOG.md` 里记一次。重试时：
- 上传中断留下的 draft release 会先删掉再重新创建；
- 已经发布但文件不完整的 Release 会补传；已经完整的 Release 不会被覆盖；
- 本地仓库落后于 `origin/main` 时会先 fast-forward；本地有还没推送的 commit 时拒绝发布，避免把没检查过的改动推出去。

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
cd ~/pvzhe/work/autoupdate && python3 -I autoupdate.py --github-repo OWNER/NAME     # 同时发布到 GitHub Release
```

代码（`pe_anycpu.py`、`loadtest/`）取自脚本所在的仓库目录；运行数据（状态、临时文件、构建目录）放在 `$PVZHE_WORK`，默认是 `~/pvzhe/work`。发布到 GitHub 需要：本仓库目录是 `OWNER/NAME` 的 clone（`origin` 指向它，脚本会核对），在 `main` 分支上；`gh` 已登录有写权限的账号；git 推送和提交身份也用这个账号。如果全局 git 配置里有别的凭据或身份，在仓库里单独设置：

```bash
git config --local --add credential.https://github.com.helper ''
git config --local --add credential.https://github.com.helper '!gh auth git-credential'
git config --local user.name <账号>
git config --local user.email <id>+<账号>@users.noreply.github.com
```

第一行的空值会清掉全局的 credential helper，必须放在前面。自动生成的 commit 和 Release 都使用 UTC 时间。

cron 示例（每天 12:00；需要指定时区的话，在 crontab 开头加 `CRON_TZ=<时区>`）：

```
0 12 * * * cd $HOME/pvzhe/work/autoupdate && mkdir -p logs && LANG=C.UTF-8 /usr/bin/python3 -I autoupdate.py --github-repo OWNER/NAME >> "logs/$(date +\%Y-\%m).log" 2>&1
```

密钥（夸克 Cookie、OpenList token）在运行时从本机 OpenList 读取，不会写进仓库或日志。
