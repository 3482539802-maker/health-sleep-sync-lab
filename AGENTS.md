# 项目开发与交接

中文沟通。新使用者读 README、docs/SETUP.md、docs/REPRODUCE.md；开发读 docs/DEVELOPMENT_HANDOFF.md、docs/AUTOMATION.md、docs/PRIVACY.md，诊断读 docs/TROUBLESHOOTING.md。

使用当前面板、automation 和 sleep_repair；历史顶层CLI没有完整受管理启动／双进程保护，不作为新写入入口。默认离线生成，全空日期不要求健康模板；整体提前、原评分和已有睡眠替换按需备份。手机原版单晚删除同步、云分段/汇总双空、电脑旧源/队列/字典清空后重建，不运行旧自动删云马上重建。

保留固定方案、基线、哈希、范围、来源/类别/汇总写前检查、每批实际读回、全部 started/失败记录、会话锁、主进程/后台 guard 和新PID门控、退出UID双协议隔离及应用停用。只读就绪有限重试，健康写不自动重试。带 started 不重跑；新的固定恢复复制正确快照，不重新随机生成、不修改运动。

睡眠来源必须当前账号登记穿戴设备，类别来自原版字典；查询source1不是汇总请求source2。评分none/original/estimate，V2估算上限95，非华为官方和非医学评分；旧计划/模型兼容不改。手机显示、云、延迟及开表长期验收分别记录。关表短期通过不代表永久解决；详情记录时间云指定仍无已验证方法。

真实配置/健康数据/计划/截图/日志/账号/设备或云ID/凭据/机器路径/厂商源码不入公开目录；仅原创代码、泛化文档、合成模板和人工测试。发布显式白名单、审查工作树/index/完整历史，项目署名和contributors@users.noreply.github.com。用户当前明确授权优先；不扩大真实操作到其他日期、Root手机、换包、配对或批量。

验证：python -m unittest discover -s tests -v；修改JS运行node --check及tests/native_upload_source.js、tests/native_sleep_cleanup.js。新sleep_repair通用代码用人工测试，同一方法的私有单晚已手机验收，本轮发布不重新写真实健康。保留用户已有改动，结果说明修改、验证与实际限制。
