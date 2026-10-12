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


class AdbLaunchError(ValueError):
    def __init__(self, command, error):
        super().__init__('ADB child could not start or timed out; inspect privately')
        self.command = command
        self.winerror = getattr(error, 'winerror', None)
        self.errno = getattr(error, 'errno', None)
        self.timeout = getattr(error, 'timeout', None)
        self.stdout = getattr(error, 'stdout', None)
        self.stderr = getattr(error, 'stderr', None)


def adb_action(command):
    actions = {('version',): '检查ADB版本', ('shell', 'getprop', 'ro.product.model'): '检查模拟器型号',
               ('shell', 'getprop', 'ro.product.board'): '检查模拟器主板',
               ('shell', 'getprop', 'ro.product.cpu.abi'): '检查模拟器CPU',
               ('shell', 'am', 'force-stop'): '停止电脑运动健康',
               ('shell', 'am', 'start'): '启动电脑运动健康',
               ('shell', 'su', '-c', 'id'): '检查模拟器Root',
               ('shell', 'pidof'): '检查健康进程'}
    return next((label for prefix, label in actions.items() if tuple(command[:len(prefix)]) == prefix), 'ADB连接命令')


def adb_message(error):
    action = adb_action(error.command)
    if isinstance(error, AdbLaunchError):
        if error.timeout is not None:
            return f'{action}超时（{error.timeout}秒）。请检查模拟器是否响应；本次命令没有自动重试。'
        reason = {2: '找不到ADB程序，请检查私有配置的 adb 路径', 3: 'ADB目录不存在，请检查 adb 路径',
                  5: 'Windows拒绝启动ADB，请检查文件权限或安全软件拦截',
                  193: 'ADB不是当前Windows可运行的程序，请检查文件是否损坏或版本不匹配'}.get(error.winerror or error.errno, 'Windows未能启动ADB，请检查路径、权限和程序依赖')
        code = error.winerror or error.errno
        return f'{action}失败：{reason}' + (f'（系统错误 {code}）。' if code else '。')
    code = error.returncode & 0xffffffff
    system_reasons = {0xc0000142: 'Windows初始化ADB失败（DLL初始化失败）',
                      0xc0000135: 'Windows找不到ADB所需DLL',
                      0xc000007b: 'ADB或依赖DLL的格式／架构不匹配'}
    if code in system_reasons:
        return f'{action}失败：{system_reasons[code]}，错误码 0x{code:08x}。请关闭面板后重开；仍失败时检查ADB依赖或重启电脑。这个错误与三环／睡眠输入数值无关。'
    output = (error.stderr or b'').decode('utf-8', 'replace').lower()
    if 'offline' in output:
        reason = '模拟器处于offline状态，请确认模拟器已启动并连接'
    elif 'unauthorized' in output:
        reason = 'ADB连接未授权，请检查模拟器调试授权'
    elif 'not found' in output or 'no devices' in output:
        reason = '未找到配置的模拟器，请检查连接和私有配置的 serial 字段'
    else:
        reason = '命令未成功，完整输出已保存到私有失败记录'
    return f'{action}失败（返回码 {error.returncode}）：{reason}。'


def user_message(error):
    if isinstance(error, FileExistsError):
        return '保存操作记录时发现同名文件，已停止以保留原证据。请查看失败记录定位冲突；不要删除计划、日志或写入标记。'
    # A safe wrapper must not hide an actionable Windows launch/connection code.
    current, seen = error, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (AdbOperationError, AdbLaunchError)):
            return adb_message(current)
        current = current.__cause__ or current.__context__
    if isinstance(error, PanelOperationError):
        return error.user_message
    # Match known authored validations only. Never echo arbitrary native exceptions.
    mappings = {
        'Registered sleep device unavailable or ambiguous': '没有唯一可用的已登记穿戴设备睡眠来源，尚未写入。请核对设备登记；存在多个来源时在私有配置选择 sleep_upload_device_code。随机睡眠不需要历史记录模板。',
        'Sleep registered source/category not adopted': '睡眠的穿戴设备来源或汇总设备类别未被采用，已停止。请查看私有读回记录，不要重复执行已有写入标记的计划。',
        'Registered upload source unavailable': '未取得已登记的上传设备来源，尚未写入。请确认电脑原版已登录并完成设备登记；这与所选日是否有健康数据无关。',
        'Existing sleep requires backup and phone selected-night deletion before upload': '所选晚已有睡眠或电脑旧源，尚未写入。点击“读取原数据（按需）”保存备份，再在手机仅删除所选晚并同步；空白日期不需要此步骤。',
        'Original data required by selected options': '整体提前或保留原评分需要对应原数据，请先读取原数据；无原记录可改用随机睡眠并取消保留原评分。',
        'Target exceeds awake minute capacity': '目标超过允许清醒时段可合理分配的容量。请扩大时段、增加活动小时或降低目标。',
        'Step target exceeds awake minute capacity': '步数超过允许清醒时段可分配的容量。请扩大时段或降低步数。',
        'Movement conflicts with zero active hours': '非零运动目标与零活动小时矛盾。请提高活动小时，或将该项留空让程序分配。',
        'Movement conflicts with existing active hour capacity': '运动目标与原活动小时容量冲突。请设置可容纳运动的活动小时目标或调整时段。',
        'Offline plan needs execution backup and review before apply': '当前是离线计划，请先点击执行前备份／核验，查看完整方案后再执行。命令行请先运行bind并使用返回的plan_path。',
        'Only offline plans need execution backup': '当前已有可执行方案，请核对完整预览后继续；不要重复生成覆盖原备份。',
        'No unique sleep source in execution backup': '已生成合成睡眠，但执行前备份没有唯一可用设备来源，尚未写入。请核对原版同步数据。',
        'Original score missing; backup before phone deletion': '原评分备份缺失，尚未写入。保留评分必须在手机删除前备份；若只需合成睡眠，请取消保留评分并重新生成。',
        'Synthetic sleep outside selected-night cleanup window': '新睡眠超出所选夜间清理窗口，已停止。请核对起床日和入睡／醒来区间。',
        'Cloud backup incomplete; no writes performed': '云端备份读取未完成，尚未写入。请确认电脑原版已登录并联网。',
        'PC CPU mismatch': '模拟器CPU类型与电脑实验配置不符，已停止。请检查是否连接到配置的电脑模拟器。',
        'PC profile mismatch': '模拟器型号或主板与已核实配置不符，已停止。请检查ADB目标和私有配置。',
        'Installed version mismatch': '电脑运动健康的安装版本与适配版本不同，已停止。请核对应用版本后再运行。',
        'Package UID missing': '未能读取电脑运动健康的安装信息，已停止。请检查模拟器应用是否已安装。',
        'Expected one running original daemon': '电脑健康后台进程未就绪或有多个匹配进程，已停止。请检查模拟器应用状态。',
        'Existing forward retained; choose a free port': '配置的本地Frida端口已有连接转发，已停止。请检查旧会话是否结束，不要直接删除运行中会话的连接。',
        'Real data must be outside the public repository': '配置、计划和备份目录必须放在公开仓库外。请重新选择本机私有文件或目录。',
        'Unsupported automation plan': '该文件不是当前面板支持的计划格式。请使用面板生成的plan或batch_plan文件。',
        'No records': '所选范围没有供整体提前或旧计划使用的原睡眠。请检查起床日；无原记录可改用随机睡眠并取消保留原评分。',
        'Missing source': '睡眠原记录缺少有效来源标识，已停止。请核对原版数据和私有备份。',
        'Mixed type/source': '睡眠备份混合多个设备来源或数据类型，当前不能自动重建。请核对所选晚。',
        'Gap in target': '睡眠原记录含时间空档，当前要求连续记录，已停止生成。请核对所选晚及私有备份。',
        'Multiple nights are not supported': '所选睡眠范围包含超过一晚的数据，当前不能自动合并。请核对起床日。',
        'Unsupported stage: current planner supports shallow/deep/REM only': '原睡眠含当前整体提前模式不支持的阶段。可检查备份，或切换随机合成模式后重新生成。',
        'Unique sleep summary required': '所选日期睡眠日汇总缺失或不唯一，请先在原版正常同步后核对起床日。',
        'Local sleep includes another session; inspect selected-night deletion scope': '电脑本地睡眠包含超出所选晚的另一段，已停止，防止扩大清理范围。请检查私有备份。',
        'Plan differs from deterministic backup/request/seed': '保存计划与原备份、请求或种子不一致，校验未通过。请重新生成或打开未改动的计划，保留原证据。',
        'Unique day sport total required': '所选日期运动日汇总缺失或不唯一，请先在原版正常同步后核对日期。',
        'An existing exercise minute is required as source template': '该日没有可用的原锻炼分钟作为新增模板。请保持锻炼目标不改，或选择有可用原记录的日期。',
        'Existing active-hour source required': '该日没有可用的原活动小时模板，当前不能新增活动小时。请保持该项不改。',
        'More selected hours than target increase': '优先新增活动小时数量超过本次需要新增的小时数。请减少优先小时或提高活动小时目标。',
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
    mappings.update({
        'Sleep summary request scope invalid': '睡眠汇总的日期、请求来源或时区不正确，已在写入前停止。请重新生成并核对新计划。',
        'Sleep target is not the registered wearable; create and review a new plan': '该睡眠计划使用了手机或不匹配的设备来源，已在任何健康写入前停止。请用新版重新生成并核对计划，保留旧证据。',
        'Sleep summary wearable category missing or mismatched; create a new plan': '睡眠汇总缺少正确的穿戴设备类别，手机可能无法显示详情，已在写入前停止。请用新版生成新计划。',
        'Sleep summary disagrees with minute timeline': '睡眠汇总起止或阶段时长与分段不一致，已在写入前停止。请检查私有计划。',
        'Original score missing or invalid; backup a positive score before phone deletion': '备份没有有效的原评分（需1–100分）。请先保存删除前备份，或选择不提交评分／脚本估算。',
    })
    return mappings.get(str(error), '操作停止。请查看私有任务目录中的失败记录；已有写入标记的计划不要重复执行。')
