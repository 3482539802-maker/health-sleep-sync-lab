# 开发与操作交接

## 当前入口

新使用者先读 [环境搭建](SETUP.md) 和 [完整复现](REPRODUCE.md)。README 为公开首页。面板／automation 是离线生成、绑定及执行入口；sleep_repair 是六域只读快照和从正确快照固定恢复的独立入口。历史 python -m sleep_sync_lab 没有受管理启动和完整双进程保护，不作为新操作入口。

## 功能与方法证据

- 生成默认离线，不启动 ADB。空白日期支持从目标构造三环、配套步数、随机睡眠和汇总；存在原值才比较，缺字段跳过，查询失败不当空。
- 自定义四运动目标留空不改，时段空不限。整体提前需要原阶段，恢复原评分需有效备份；已有睡眠替换也先备份绑定，手机仅删所选晚并同步。
- 睡眠使用已登记穿戴来源和字典类别；汇总请求 source2／deviceCode0，读取可能 source1。全部计划／批量在任何健康写前检查来源、类别和目标一致性，不退回手机来源。
- 评分 none／original／estimate；新 stage_estimate_v2 上限95，保存 raw_score、模型和来源。V1及缺模型的旧请求沿用旧规则，不改旧计划；估算非官方、非医学评分。
- 独立研究实例退出停用应用并保留 UID 双协议隔离，受控启动重建隔离／校验已安装服务后启用，主进程与后台同时 guard、ART deoptimize、暂停新进程。
- 固定恢复从自己此前正确六域快照复制时间、阶段和完整 professionalSleep／评分，不抽样。新任务备份现状与邻日，手机清源，双次云空，限定电脑清理，200分钟批次、完整窗与汇总读回，独立 verify。

同一方法的私有单晚操作已完成真实云分段／汇总检查及手机起止、阶段、来源和评分验收；关表条件下手机正常同步后独立查询正确。新公开 sleep_repair 是参数化版本，使用人工数据测试，本轮没有重新写真实账号。全空日期组合上传、真实多日、重新开表与长期稳定仍需分别验收。

## 源码地图

| 文件 | 责任 |
| --- | --- |
| sleep_sync_lab/panel.py | 中文面板、输入、固定预览、源码更新检测、线程与私有错误 |
| automation.py、automation_model.py | prepare／bind／review／apply、基线、批次、完整窗及结果 |
| generation.py、standalone.py | 离线目标、按需绑定、无原值生成、登记来源 |
| sleep_repair.py | 六域 snapshot、固定目标 prepare／review、独立 apply／verify，不改运动 |
| sleep_preflight.py、sleep_scoring.py | 穿戴来源／类别／汇总一致性、版本化估算与来源 |
| automation_client.py、automation_native.js | 原版桥接、双进程保护、限定单晚清理、来源数量SELECT |
| emulator_startup.py、host_process.py | 指定实例启动、跨开机停用、Root／ADB／Windows进程 |
| sleep_generation.py、presets.py、batch.py | 自然阶段V2、私有默认预设、逐日独立计划和全批预检 |
| audit.py、diagnostics.py、errors.py | 证据、哈希、时间线、异常链和安全中文提示 |
| tests | 全部人工 fixture；JS 原生来源／SQLite范围／回滚测试 |

## 不可省略的行为

固定计划、源快照哈希、现状备份、日期／时区／来源和授权范围、写前核验、私有日志与会话锁、各 started、每批实际读回和停止后保护。健康写不自动重试。已执行任务不能删标记再 apply，新恢复必须独立且保存完整当前基线，不使用回流的混合数据作为正确目标。

睡眠现有记录使用手机原版单晚删除并同步，云分段／汇总都空才清电脑旧源和已完成删除队列／字典。电脑自动删云马上重建曾有延迟删新段及旧源合并问题，已停用。诊断启动曾发生非预期后台删除，不能抹掉历史事实；现在同时保护主进程／后台并检查新PID。

固定恢复另查核心、当日睡眠统计、字典和 sync_cache 数量；非空未知缓存、范围外旧源或清理未归零停止，不删除未知缓存。清理必须在已确认云空后且只在 review 范围，明确 --allow-pc-cleanup；新代码只在人工数据验证。

## 持续同步与记录时间

原版穿戴文件同步有独立解析／逐分钟插入路径；手机删一晚或清电脑源不清手表文件。关表可做隔离验证，但手机可能已有缓存，不凭版本时间唯一认定上传端。recordId 一样也可能阶段已变，核验实际内容与完整汇总；手机显示、延迟读回、开表及长期行为分别记录。

详情记录时间读取本地修改时间；电脑本地限定元数据实验曾成功并恢复，云端多个纯内存转换探测未采用指定时间。汇总 generateTime 不代替详情时间，不提供已可改手机该栏的选项。

## 验证与发布

运行 python -m unittest discover -s tests -v；node --check sleep_sync_lab/automation_native.js；node tests/native_upload_source.js；node tests/native_sleep_cleanup.js。新 sleep_repair 回归覆盖来源／字段、固定目标、空云、运动／邻日、旧源范围、缓存、失败标记、旧尾段／阶段覆盖。完整数量以本次测试实际结果为准，旧文档的阶段性数量不是当前数量。

真实配置、快照、计划、日志、截图、ID、凭据、机器路径及厂商二进制／源码放公开仓库外。发布显式文件白名单、扫描工作树／index／全部历史与元数据、项目署名+noreply，并验证远端精确提交。检查用户已有未发布改动，不覆盖它们。任何真实写入另按当前具体授权，不以发布测试重复健康操作。
