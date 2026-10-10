"""Safe user-facing errors; never display raw native request or traceback text."""


class PanelOperationError(ValueError):
    def __init__(self, message):
        super().__init__(message)
        self.user_message = message


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
    }
    return mappings.get(str(error), '操作停止。请查看私有任务目录中的失败记录；已有写入标记的计划不要重复执行。')
