# 脚本面板与自动化

## 使用

Windows安装带Tk的Python并安装requirements.txt后，双击run_panel.cmd，或运行：

```console
python -m sleep_sync_lab.panel --config /private/config.json --runs /private/runs
```

配置参考examples/config.template.json。需要已登录原版17.0.8.310的独立电脑安卓模拟器、电脑模拟器Root和Frida server；不在手机安装／注入，不修改系统时间。真实配置、记录、计划和结果全部放仓库外。

三环与步数字段留空表示不改。整数为固定值，“最小值-最大值”为区间随机整数。活动小时可填写优先新增的整点小时，其他新增小时从允许时段选择。已有活动小时不主动改成未活动。

睡眠日期按起床日选择。整体提前保留原阶段顺序；提前分钟支持单值或随机区间。随机模式同时满足入睡窗口、醒来窗口和总时长；23:00–01:00表示前一日23点到起床日1点。总时长4–12小时，生成浅睡、深睡、REM连续一分钟分段，深睡倾向前段、REM倾向后段。这是合成编辑序列，不能当监测结果或临床睡眠模型。混合来源、同次多晚、清醒分段或午睡自动替换暂不支持，遇到停止。

生成后查看计划日期、目标、睡眠起止、阶段分钟与电脑旧睡眠清理范围。随机结果固定，执行不重新抽取。包含睡眠时，勾选所选整晚删除重建确认，再执行。每批上传读回核验；结果显示请求值与实际云端值，未采用不报告成功。

## 无界面命令

复制examples/request.template.json到私有目录编辑；模板为人工示例，删除sleep项即不改睡眠。

```console
python -m sleep_sync_lab.automation prepare --config /private/config.json --request /private/request.json --job /private/jobs/new-job
python -m sleep_sync_lab.automation review --plan /private/jobs/new-job/plan_private.json
python -m sleep_sync_lab.automation apply --plan /private/jobs/new-job/plan_private.json
```

睡眠执行需显式加--allow-sleep-delete，仅对已查看的单晚计划使用。前两步只备份／生成计划，不修改健康记录。新日期创建新任务目录，不覆盖旧计划或删started标记重复执行。

## 方法与证据边界

| 项目 | 方法 | 证据及限制 |
| --- | --- | --- |
| 热量 | 限定时段重分配采用明细，保留备份日汇总残差，同步日总数 | 既有明细进入手机已验收；降低日总数可能忽略，逐柱取整仍可与总数不同 |
| 步数 | 指定时段分配目标，默认优先已有活动小时，同步总数 | 既有修改已获手机反馈；仍可能触发其他派生统计 |
| 锻炼 | 增加未占用分钟的强度记录，同步日总数；降低只尝试总数 | 面板新增流程已实现及模拟测试，尚未单独通过手机验收；已观察降低不采用 |
| 活动小时 | 上传ACTIVE_HOUR/isActive=1及日总数 | 通用适配器新增一个指定小时与日总数已云读回，手机待验收 |
| 睡眠 | 备份云分段与电脑本地范围，清电脑该晚、按时间删云，分批重建汇总 | 手机手动删除再重建已通过；新自动删除已有静态定位与模拟验证，未新增真实删除测试或手机验收 |

新自动睡眠路径与既有手机手动删除路径不同。能执行电脑／云端操作，不保证Pura本地旧记录同步清除；手机仍有旧尾段时需诊断，或在手机原版删除所选整晚后用新计划重建。当前正确记录没有为了验收面板再次删除。原评分可保留备份值，不能称重新计算；不保留时提交评分0，等待原版处理或显示未评分。

日统计较大值优先，随机目标低于已有值可能不采用。运动目前不批量删旧记录，不用isForce假装强制覆盖。睡眠会真实删除重建，不保证recordId、版本或所有派生统计恢复。

启动先验证电脑环境，短暂断开电脑应用网络，加载临时后台写入guard后联网，只放行任务请求。退出先停电脑原版，再撤guard、自建forward和IPv4/IPv6规则。guard不是华为限制，也不装手机；不承诺覆盖任意未知写入路径。

## 失败恢复

任务保存backup_private.json、plan_private.json、review_private.json、写入started标记、响应与读回。失败不自动重试；全局锁防并发，已有apply_started任务不能再执行。强制杀进程可能残留锁或规则，先检查，不盲目删锁重跑。睡眠中断可能暂时为空或仅重建部分；备份保留，但恢复需新计划，不保证原服务器身份。

验证命令：python -m unittest discover -s tests -v，以及node --check sleep_sync_lab/automation_native.js。测试仅用人工数据，覆盖随机范围、固定计划、跨午夜、阶段合计、热量残差、失败与禁止重试、删除重建读回。模拟测试不代替手机验收，不证明永久不回流或不可检测。
