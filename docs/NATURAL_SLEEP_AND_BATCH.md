# 合成睡眠结构与默认方案批量处理

## 合成规则及来源

新建随机睡眠使用`stage_model=natural_v2`。浅睡合并N1/N2，深睡对应N3，REM保留独立标签。连续区段有长短变化，深睡集中前半夜，后半夜可以没有深睡，REM后段较长；周期长度加权偏向约100分钟。V2允许少量3–9分钟区段，在相邻阶段之间重新分配时间，使超过50分钟的连续区段少见，超过65分钟的可拆分区段优先拆开；每晚总时长和各阶段总分钟保持底层V1结果不变。这是编辑器的启发式选择，不是医学正常值判断，也不是对可穿戴监测算法的复现。

依据的网页均为实际读取的正文：

- [NIH/NHLBI：Sleep Phases and Stages](https://www.nhlbi.nih.gov/health/sleep/stages-of-sleep)：周期通常80–100分钟，每晚约4–6个，早段更多深睡、后段更多REM。
- [Cleveland Clinic：Sleep](https://my.clevelandclinic.org/health/body/12148-sleep-basics)：通常90–120分钟一个周期；前次REM较短，后续较长。

两来源对典型周期的表述有所差别，不据此设立人人通用的硬性健康标准。手表浅／深／REM与脑电睡眠分期也不是同一测量方法。随机生成暂不编造清醒段；新版随机替换可接受同一来源、连续分钟的原备份含清醒，再生成浅／深／REM。整体提前模式仍保留原算法边界。

旧随机计划没有`stage_model`时，继续用最早生成算法校验；保存的`natural_v1`计划仍用V1生成器，`natural_v2`用于新建请求。已经保存的计划不会重新抽取或按新模型转换。修改代码后重开面板加载新版。

独立离线运行，不会启动模拟器、连接账号或上传：

```console
python -X utf8 -m sleep_sync_lab.sleep_generation --count 10 --minutes 480 --start 00:45 --seed 1
```

## 私有默认预设

复制`examples/preset.template.json`到仓库外，并将该对象放入私有配置的`default_preset`字段。模板数字为人工示例，不是参与者真实目标。每个字段含`range`硬范围与`preferred`高概率区段；时间字段用当天午夜起的分钟数。使用离散钟形混合分布，窄分布偏向高概率区段，宽尾保留其余合法值的机会。入睡／醒来与总时长同时满足，执行时不再抽样。

面板“生成方案”选择“默认方案”，三环按预设独立抽样，默认不改步数。抽样不高于原日总数的三环项目完全跳过，其余项目继续；原数值不会为了落入范围而下调。运动新增或改变的分钟避开原睡眠与目标睡眠的并集；新增活动小时必须完整落在清醒时段。清醒整点小时不足时，该活动小时项目保持原值，计划明确列出原因。既有运动记录不主动删除。

原评分保留只复制备份，不根据合成分段重新评分；面板可取消保留。默认方案输入框显示范围，但生成时使用私有预设；要用输入框定制时切回“自定义”。

## 日期区间与执行

起始日期和批量截止日期均含当天；截止留空是单日，一次最多31天。批量仅用于默认方案，每晚按起床日。每个日期独立派生随机种子，计划保存具体结果，并避免整套抽样目标及入睡／时长全部相同。相同单个字段偶尔重复是正常的；低于原值跳过后，实际日总数也可能相同。

生成批量计划只读，全部日期备份及计划完成后才保存批量清单。清单绑定每个逐日文件的SHA-256及日期；删除范围不能互相重叠。生成后参数区自动收起以留出预览空间，点击“展开/收起参数”可继续编辑。

执行前先保存完整清单，在手机原版仅删除清单所选各晚并完成同步，再勾选手机清理确认。先核验全部日期云分段及汇总为空、运动基线一致，才写批量started；随后每个日期再核验自身基线、使用逐日started、批次响应／读回与result。云端未采用或任何错误都会停止后续日期。批量不是事务：此前成功日期和失败日期的部分写入不会自动回滚，睡眠中断可能为空或部分重建。保留整批及逐日证据，不能删除标记重跑；尚未执行日期可另建新范围计划。[睡眠清源与电脑隔离](SLEEP_RECURRENCE.md)。

批量记录额外包含`batch_apply_started_private.json`、逐日汇总结果和`completed_dates`；原有事件、源码哈希、进程锁、后台guard和退出清理全部保留。真实批量手机显示与长期同步仍需独立验收，人工测试和单日只读生成不代表多日云写入通过。

CLI同样保留prepare/review/apply：请求文件中的`preset`对象使用私有预设；可在仓库外调用`preset_request`构建该请求。

```console
python -m sleep_sync_lab.batch prepare --config /private/config.json --request /private/request.json --start 2001-01-01 --end 2001-01-03 --job /private/jobs/batch
python -m sleep_sync_lab.batch review --plan /private/jobs/batch/batch_plan_private.json
python -m sleep_sync_lab.batch apply --plan /private/jobs/batch/batch_plan_private.json --allow-sleep-delete --phone-nights-deleted
```

这些日期为人工示例。没有原睡眠、混合来源、多晚／午睡或不支持的阶段仍会停止生成，不猜测或扩大删除范围。
