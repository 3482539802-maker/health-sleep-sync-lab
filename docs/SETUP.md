# 从新电脑搭建环境

手机继续使用现有原版运动健康及自己的账号／配对，不在手机 Root、安装修改包或注入。账号由使用者自己登录，仓库不接收密码、验证码或令牌。

## 1. 工具与 Python

需要 Windows、Git、带 Tk 的 Python 3.10+、Android Platform Tools 和可 Root 的独立 LDPlayer 9 研究实例。从 [Git](https://git-scm.com/downloads)、[Python](https://www.python.org/downloads/)、[ADB](https://developer.android.com/tools/releases/platform-tools) 和模拟器官方渠道取得。自动启动使用 ldconsole.exe，其他模拟器启动管理未经验证。

按 [README](../README.md#安装与启动)克隆并创建仓库外的 $labRoot 和 $py，依赖装进后续运行脚本的同一 Python 环境。检查 Tk 和 Frida：

```powershell
& $py -m tkinter
& $py -c 'import frida; print(frida.__version__)'
```

第一条应打开示例窗口，第二条应返回 requirements 的固定版本。关闭 Tk 示例即可。

## 2. 研究实例与原版登录

新建独立实例，记录序号和名称，启用模拟器 Root、仅本机 ADB。示例变量必须换成自己的工具和目标；手机不用接 USB。

```powershell
$adb = (Get-Command adb).Source
$serial = 'emulator-5554'
& $adb devices
& $adb -s $serial shell getprop ro.product.cpu.abi
& $adb -s $serial shell getprop ro.product.model
& $adb -s $serial shell getprop ro.product.board
& $adb -s $serial shell 'su -c id'
```

需要 x86／x86_64，Root 返回 uid=0。序号 0 通常对应 emulator-5554，其他序号与自己的实例和启动配置匹配。先确认是隔离电脑实例，不能仅因 ADB 在线就设置 emulator_verified=true。

在该实例从可信官方渠道安装未修改的运动健康 **17.0.8.310**，自己登录与手机相同账号。仓库不提供 APK；无法取得对应原版或已安装其他版本时先适配，不直接写入。

```powershell
& $adb -s $serial shell dumpsys package com.huawei.health | Select-String 'versionName='
& $adb -s $serial shell am force-stop com.huawei.health
```

首次登录／下载可能触发正常同步，完成登录后停电脑应用，再经受保护会话操作。穿戴睡眠来源须已登记在账号；无历史记录可以新增，无登记来源会停止。无需把手表改配对到模拟器。

## 3. 安装 Frida server

从 [Frida 官方 Releases](https://github.com/frida/frida/releases/tag/17.23.1)获取匹配模拟器 ABI 的 frida-server-17.23.1-android-x86_64.xz 或 x86 包。解压，命名 gt2-frida-server，放仓库外 $labRoot。不要下载手机 ARM 包注入实体手机。

```powershell
$server = Join-Path $labRoot 'gt2-frida-server'
& $adb -s $serial push $server /data/local/tmp/gt2-frida-server
& $adb -s $serial shell "su -c 'chmod 700 /data/local/tmp/gt2-frida-server'"
& $adb -s $serial shell "su -c '/data/local/tmp/gt2-frida-server -l 127.0.0.1:27042 -D'"
& $adb -s $serial shell pidof gt2-frida-server
```

服务只监听模拟器回环地址，由 ADB 临时转发。有的 su 后台已启动但命令不返回，先查 PID，不启动多份。后续自动启动记录该文件 SHA-256，只启动校验一致的已有服务。

## 4. 私有配置与默认预设

```powershell
$config = Join-Path $labRoot 'config.json'
Copy-Item examples/config.template.json $config
$cfg = Get-Content -LiteralPath $config -Raw | ConvertFrom-Json
$cfg.adb = $adb
$cfg.serial = $serial
$cfg.expected_emulator_model = (& $adb -s $serial shell getprop ro.product.model).Trim()
$cfg.expected_emulator_board = (& $adb -s $serial shell getprop ro.product.board).Trim()
$cfg.emulator_verified = $true
$preset = Get-Content examples/preset.template.json -Raw | ConvertFrom-Json
$cfg | Add-Member -NotePropertyName default_preset -NotePropertyValue $preset -Force
[IO.File]::WriteAllText($config, ($cfg | ConvertTo-Json -Depth 30), [Text.UTF8Encoding]::new($false))
notepad $config
```

填写自己的目标日期、时区和预设范围。模板日期／范围是人工示例，不能直接作为实际目标，也不保证适合任何人。写 UTF-8 无 BOM 避免部分 PowerShell 版本 JSON 的 BOM 问题。

包名、app_version、transport_class、gson_factory_class 是当前适配参数，不猜测更改。frida_remote_port 为电脑临时转发端口，应空闲。多个来源时私有配置指定自己的 sleep_upload_device_code／upload_device_code，不复制其他人的设备 ID。

## 5. 开机保护与自动启动

首次设置要求研究实例已打开并核实，服务已安装：

```powershell
$console = Read-Host '输入自己的 ldconsole.exe 完整路径'
$instanceName = Read-Host '输入研究实例名称'
& $py -m sleep_sync_lab.emulator_startup --config $config --console $console --index 0 --name $instanceName --state-dir (Join-Path $labRoot 'emulator-startup')
```

index 必须对应自己的实例。命令核验环境、保存服务哈希、建立 UID 双协议隔离并停用电脑应用，不上传或删除健康记录。后续连接先重建隔离，再启用应用、保护主进程和后台，最后联网；退出停止／停用并保留隔离。详见[启动说明](EMULATOR_STARTUP.md)。

失败先检查私有诊断，不删锁或手动启用绕过保护。如果只剩窗口而 VM 进程已停，ADB 会超时；保留原计划，在确认应用停用许可后受控重启同一个实例。

## 6. 启动与只读快照

```powershell
& $py -m sleep_sync_lab.panel --config $config --runs (Join-Path $labRoot 'runs')
```

窗口打开不写健康数据。也可先读六域和电脑来源数量：

```powershell
$readDir = Join-Path $labRoot ('reads/' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
& $py -m sleep_sync_lab.sleep_repair snapshot --config $config --out $readDir
```

快照保存自己的数据，不自动认定正确。查询失败不能当空数据；检查登录／网络／版本／私有诊断后再继续[复现步骤](REPRODUCE.md)。
