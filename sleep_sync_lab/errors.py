"""Safe user-facing errors; never display raw native request or traceback text."""


class PanelOperationError(ValueError):
    def __init__(self, message):
        super().__init__(message)
        self.user_message = message


class AdbOperationError(ValueError):
    """Raw diagnostics available only to private failure files."""
    def __init__(self, command, result):
        super().__init__('ADB operation failed; inspect privately')
        self.command = command
        self.returncode = result.returncode
        self.stdout = result.stdout
        self.stderr = result.stderr


def user_message(error):
    if isinstance(error, PanelOperationError):
        return error.user_message
    # Match known authored validations only. Never echo arbitrary native exceptions.
    mappings = {
        'Cloud backup incomplete; no writes performed': '云端备份读取未完成，尚未写入。请确认电脑原版已登录并联网。',
        'Selected cloud data changed after preview; create a new plan': '生成计划后，所选日期的云端数据发生变化。尚未执行本次写入，请重新生成计划。',
        'An execution marker exists; do not repeat this plan': '这个计划已有执行标记，不能再次执行。请查看实际读回结果。',
        'Another PC emulator session or stale session lock exists': '另一个电脑连接正在运行，或上次异常退出留下会话锁。请先检查连接状态。',
        'Another job or unfinished session lock exists; inspect before proceeding': '另一个任务正在执行，或有未完成任务锁。请先检查任务状态。',
        'Target is below unmodified minutes or retained summary residual; expand windows': '目标值低于未修改时段的合计。请扩大允许分配时段，或提高目标值。',
        'Not enough unused exercise minutes in allowed hours': '允许时段中可新增的锻炼分钟不足。请扩大时段或降低新增目标。',
        'Not enough inactive hours in allowed windows': '允许时段中未活动的小时数不足。请扩大时段或降低活动小时目标。',
        'Sleep/wake windows and total duration do not overlap': '入睡、醒来区间与睡眠总时长无法同时满足，请调整区间。',
        'Batch day not adopted; later dates stopped': '当前日期的云端数据未完全采用，已停止后续日期。请查看逐日结果，保留全部写入标记。',
        'Batch end precedes start': '批量截止日期不能早于开始日期。',
        'Batch supports at most 31 reviewed days': '一次最多选择31天，请缩短日期区间。',
        'Batch child plan changed': '批量计划中的某个逐日文件发生变化，已停止执行。请查看私有记录。',
        'Batch sleep deletion scopes overlap': '不同日期的旧睡眠清理范围重叠，未执行，请先核对备份范围。',
        'Explicit selected-night deletion confirmation required': '请核对当前计划包含的睡眠日期与范围，并勾选整晚删除重建确认。',
        'Phone night deletion and empty cloud segments/summary required before rebuild': '尚未确认手机旧睡眠已清空。先保存完整计划，在手机原版仅删除所选晚并正常同步；云分段和汇总都为空后才能重建。',
        'PC old sleep remains after local deletion': '电脑仍有旧睡眠或旧起止字段，已停止重建。请保留失败记录及写入标记，不要重复执行。',
        'Pending sleep deletions remain; rebuild refused': '电脑旧睡眠删除任务尚未清空，已停止重建，防止延迟删除新数据。请保留记录和写入标记。',
        'Sleep changed or old tail returned during verification': '完整睡眠窗口核验发现变化或旧尾段，已停止。请保留当前记录，不要重新执行旧计划。',
        'A new app process was paused; session stopped before further requests': '电脑运动健康出现新的未保护进程，已暂停该进程并停止任务。请查看私有记录。',
    }
    return mappings.get(str(error), '操作停止。请查看私有任务目录中的失败记录；已有写入标记的计划不要重复执行。')
