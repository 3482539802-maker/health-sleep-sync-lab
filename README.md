# Health Sleep Sync Lab

华为运动健康三环、步数和单晚睡眠的研究工具。提供中文面板、离线计划、限定执行、云端读回，以及从正确备份恢复固定睡眠的独立任务。最终验收位置是手机现有原版运动健康，手机无需 Root，也无需改变系统时间。

**新使用者从[完整复现指南](docs/REPRODUCE.md)开始，首次搭建电脑见[环境安装](docs/SETUP.md)。** 仓库包含原创工具和泛化方法，不含真实健康记录、账号、设备 ID、截图、数据库、私有路径、APK 或厂商反编译源码。公开 JSON 和测试数据均为人工示例，使用者填写自己的配置并在仓库外保存任务。

## 可以复现的流程

| 场景 | 入口与行为 |
| --- | --- |
| 空白日期新增 | 面板离线生成三环、配套步数、随机睡眠及汇总；有原值才比较，缺值跳过；生成不启动 ADB |
| 已有睡眠替换 | 先保存备份和固定方案，手机原版仅删除所选晚并同步，核验云空，清电脑旧源／删除队列后重建 |
| 恢复同一套正确阶段 | sleep_repair 从自己此前验收正确的六域快照建立新任务，保留时间、阶段、完整睡眠汇总和备份评分，不重新随机生成 |
| 显示评分 | 不提交、恢复原评分、脚本分段估算三种模式；新估算上限 95，记录非华为官方评分 |
| 模拟器关闭 | 首次配置受管理实例后，连接需要时自动启动指定实例；已运行不重启，退出停用电脑健康应用并保留隔离 |

已验证的方法包括单晚云分段／汇总恢复、手机原版起止／时长／来源／评分显示，以及手表关机条件下手机正常同步后的短期云读回。新公开固定恢复工具由同一方法泛化并通过人工测试，没有在新的真实账号上重新执行。空白日期全项目上传、真实多日批量和重新开表后的长期稳定性仍需分别验收。

## 安装与启动

适配环境为 Windows、带 Tk 的 Python 3.10+、独立电脑安卓模拟器、ADB、模拟器 Root、原版运动健康 **17.0.8.310**、Frida server **17.23.1**。Python 依赖固定在 [requirements.txt](requirements.txt)。其他应用版本需要重新核对内部类和协议。

```powershell
git clone https://github.com/3482539802-maker/health-sleep-sync-lab.git
Set-Location health-sleep-sync-lab
$labRoot = Join-Path $env:LOCALAPPDATA 'HealthSleepSyncLab'
New-Item -ItemType Directory -Force -Path $labRoot | Out-Null
py -3 -m venv (Join-Path $labRoot 'venv')
$py = Join-Path $labRoot 'venv/Scripts/python.exe'
& $py -m pip install -r requirements.txt
& $py -m unittest discover -s tests -v
```

按[环境安装](docs/SETUP.md)完成自己的配置、原版登录、Frida 服务和实例开机隔离，再启动：

```powershell
$config = Join-Path $labRoot 'config.json'
& $py -m sleep_sync_lab.panel --config $config --runs (Join-Path $labRoot 'runs')
```

run_panel.cmd 也是入口，但运行它的 Python 必须安装同一依赖；仓库没有作者电脑的配置或快捷方式。窗口打开和计划生成不写健康数据。

## 操作顺序

1. 选择自己的目标日期和项目。自定义三环／步数留空表示不改，活动时段留空表示不限；只改睡眠时四个运动目标留空并勾选睡眠。
2. 随机睡眠可以完全离线生成。整体提前需要原阶段，恢复原评分需要删除前评分；已有睡眠替换先保存备份。见[按需原数据](docs/OFFLINE_PLANS.md)。
3. 审阅固定计划。已有睡眠时手机原版仅删所选一晚并同步；空白日期无需删除。程序核验云空、穿戴来源／类别和完整目标后分批上传。
4. 核对云完整旧／新窗、实际阶段和汇总；手机正常同步核对起止、时长、阶段、来源和评分，退出电脑会话后再独立读回。
5. 旧尾段或原阶段回流时保留证据和全部 started，从自己此前正确快照创建新的固定恢复任务。见[恢复分支](docs/REPRODUCE.md#从正确快照恢复固定睡眠)，不要重跑旧任务或删标记。

## 实际边界

- 请求成功、recordId 仍在、条数正确都不足以证明实际内容或手机采用，必须核对完整时间／阶段／来源和汇总。
- 手机删一晚、清电脑源不会删除手表原始文件。穿戴文件可能重新导入并生成原阶段；关表隔离短期验收不能证明唯一上传端或永久解决回流。
- 原评分是复制备份；脚本估算不是华为官方重算或医学评分。手机数值栏不能附估算标签，工具保留评分来源。
- 详情“记录到运动健康的时间”来自本地修改时间。电脑本地元数据实验可改变并恢复该栏，云端指定该栏尚无已验证方法；generateTime 不代替它。
- 运动修改可能影响活动小时等派生统计，下调也可能不采用；内容备份不保证恢复服务器版本、审计历史或全部周月年统计。

## 文档

| 文档 | 内容 |
| --- | --- |
| [SETUP](docs/SETUP.md) | 新电脑、模拟器、ADB、登录、Frida、配置和自动启动 |
| [REPRODUCE](docs/REPRODUCE.md) | 正常新增／替换、固定恢复、手机和延迟验收 |
| [AUTOMATION](docs/AUTOMATION.md)／[OFFLINE_PLANS](docs/OFFLINE_PLANS.md) | 当前面板／CLI和按需规则 |
| [SLEEP_SCORE](docs/SLEEP_SCORE.md) | 评分规则和睡眠来源检查 |
| [SLEEP_RECURRENCE](docs/SLEEP_RECURRENCE.md) | 旧尾段、阶段重传、删除队列和清源 |
| [TROUBLESHOOTING](docs/TROUBLESHOOTING.md) | 连接、来源、详情空白和中断 |
| [DEVELOPMENT_HANDOFF](docs/DEVELOPMENT_HANDOFF.md) | 源码地图、验证和继续开发 |
| [PRIVACY](docs/PRIVACY.md) | 公开／私有分离及发布 |

历史 python -m sleep_sync_lab CLI 保留兼容，但没有当前受管理启动和完整双进程保护；新使用者采用面板、automation 和 sleep_repair。历史说明不覆盖当前流程。

源码采用 [MIT 许可](LICENSE)。不分发华为 APK、模拟器映像或 Frida 二进制，与华为无隶属关系。内部接口行为不保证长期不变，也不保证修改不可识别。
