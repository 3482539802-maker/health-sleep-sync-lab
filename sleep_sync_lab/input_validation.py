"""Field-specific offline preflight; no emulator, network or health operations."""
from datetime import date
import re
from .errors import PanelOperationError
from .model import timezone_value

TARGETS = {'calorie': ('活动热量', 0, 10000), 'exercise': ('锻炼分钟', 0, 1440),
           'steps': ('每日步数', 0, 200000), 'active': ('活动小时', 0, 24)}


class InputValidationError(PanelOperationError):
    def __init__(self, field, value, reason):
        self.field, self.value = field, value
        shown = str(value).replace('\n', ' ').replace('\r', ' ')[:160]
        super().__init__(f'{field}：{reason}。当前填写：{shown!r}。')


def normalized(text):
    return str(text).strip().translate(str.maketrans({'：': ':', '，': ',', '–': '-', '—': '-', '−': '-', '～': '-', '~': '-'}))


def number(value, field, low, high, required=False):
    if value is None or value == '':
        if required:
            raise InputValidationError(field, '', '不能为空')
        return None
    if isinstance(value, str):
        text = normalized(value)
        match = re.fullmatch(r'([+-]?\d+)(?:\s*-\s*([+-]?\d+))?', text)
        if not match:
            raise InputValidationError(field, value, '请填写整数或“最小值-最大值”，不要填写单位／小数')
        result = [int(match[1]), int(match[2])] if match[2] is not None else int(match[1])
    else:
        result = value
    if isinstance(result, (list, tuple)) and len(result) != 2:
        raise InputValidationError(field, value, '区间需要两个整数上下限')
    values = list(result) if isinstance(result, (list, tuple)) else [result]
    if len(values) not in (1, 2) or any(type(v) is not int for v in values):
        raise InputValidationError(field, value, '需要一个整数或两个整数上下限')
    if any(not low <= v <= high for v in values):
        raise InputValidationError(field, value, f'支持范围为{low}到{high}')
    if len(values) == 2 and values[0] > values[1]:
        raise InputValidationError(field, value, '最小值不能大于最大值')
    return result


def clock(value, field, allow_end=False):
    text = normalized(value)
    if not re.fullmatch(r'\d{1,2}:\d{2}', text):
        raise InputValidationError(field, value, '时间格式应为HH:MM，例如07:30')
    hour, minute = map(int, text.split(':'))
    if not (0 <= hour < 24 and 0 <= minute < 60) and not (allow_end and hour == 24 and minute == 0):
        raise InputValidationError(field, value, '小时需为0–23、分钟需为0–59，24:00仅用于活动时段结束')
    return hour * 60 + minute


def time_range(value, field, activity=False):
    parts = normalized(value).split('-') if isinstance(value, str) else value
    if not isinstance(parts, (list, tuple)) or len(parts) != 2:
        raise InputValidationError(field, value, '请填写“开始HH:MM-结束HH:MM”')
    return [clock(parts[0], field), clock(parts[1], field, activity)]


def activity_windows(value):
    field = '允许分配的时段'
    if not isinstance(value, str) or not value.strip():
        raise InputValidationError(field, value, '至少填写一个活动时段')
    ranges = [time_range(part.strip(), field, True) for part in normalized(value).split(',')]
    if any(a >= b for a, b in ranges):
        raise InputValidationError(field, value, '每个活动时段须在当日内、开始早于结束；跨午夜请拆成两段')
    ordered = sorted(ranges)
    if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])):
        raise InputValidationError(field, value, '活动时段有重叠，请合并或修改')
    return ','.join(f'{a//60:02}:{a%60:02}-{b//60:02}:{b%60:02}' for a, b in ranges)


def hours(value):
    if isinstance(value, str):
        text = normalized(value)
        if not text:
            return []
        parts = text.split(',')
        if any(not re.fullmatch(r'\d{1,2}', part.strip()) for part in parts):
            raise InputValidationError('优先新增活动小时', value, '填写0–23的整数，用逗号分隔，例如9,17')
        value = [int(part) for part in parts]
    if not isinstance(value, list) or any(type(h) is not int or not 0 <= h <= 23 for h in value):
        raise InputValidationError('优先新增活动小时', value, '每个小时需为0–23的整数')
    if len(set(value)) != len(value):
        raise InputValidationError('优先新增活动小时', value, '小时不能重复')
    return value


def date_input(value, field='日期 / 起床日'):
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(value)):
            raise ValueError
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise InputValidationError(field, value, '需要有效日期，格式YYYY-MM-DD') from None


def validate_preset(spec):
    if not isinstance(spec, dict) or spec.get('version') != 1:
        raise InputValidationError('默认方案配置', '', '需要version=1的default_preset对象')
    fields = [(key, TARGETS[key]) for key in ['calorie', 'exercise', 'active']]
    fields += [('bedtime', ('默认入睡分钟', 0, 1439)), ('wake', ('默认醒来分钟', 0, 1439))]
    for key, limits in fields:
        field, low, high = limits
        item = spec.get(key)
        if not isinstance(item, dict) or not isinstance(item.get('range'), list) or len(item['range']) != 2:
            raise InputValidationError('default_preset.' + key, '', '需要两个整数组成的range')
        a, b = number(item['range'], field, low, high, True)
        preferred = item.get('preferred', [a, b])
        if not isinstance(preferred, list) or len(preferred) != 2:
            raise InputValidationError(field + '高概率区段', preferred, '需要两个整数')
        number(preferred, field + '高概率区段', a, b, True)
    duration = spec.get('duration_range')
    if not isinstance(duration, list) or len(duration) != 2:
        raise InputValidationError('默认总睡眠分钟', duration, '需要两个整数上下限')
    number(duration, '默认总睡眠分钟', 240, 720, True)
    bed_lo, bed_hi = spec['bedtime']['range']
    wake_lo, wake_hi = spec['wake']['range']
    if not bed_hi < wake_lo:
        raise InputValidationError('默认入睡／醒来区间', '', '默认方案入睡区间必须早于醒来区间且在同一日')
    if bed_lo + duration[0] > wake_hi or bed_hi + duration[1] < wake_lo:
        raise InputValidationError('默认睡眠区间组合', '', '入睡、醒来和总时长没有可同时满足的组合')


def validate_request(request):
    if not isinstance(request, dict):
        raise InputValidationError('请求', '', '需要对象格式')
    if request.get('preset'):
        validate_preset(request['preset'])
    for key, (field, low, high) in TARGETS.items():
        number(request.get(key), field, low, high)
    activity_windows(request.get('activity_windows', '12:00-14:30,18:00-24:00'))
    selected = hours(request.get('active_hours', []))
    if selected and request.get('active') is None and not request.get('preset'):
        raise InputValidationError('优先新增活动小时', selected, '填写优先小时还需要设置活动小时目标')
    sleep = request.get('sleep')
    if sleep:
        if sleep.get('mode', 'shift') == 'shift':
            number(sleep.get('advance_minutes', 120), '提前分钟', -720, 720, True)
        elif sleep.get('mode') == 'random':
            bed_lo, bed_hi = time_range(sleep.get('bedtime_range'), '入睡区间')
            wake_lo, wake_hi = time_range(sleep.get('wake_range'), '醒来区间')
            if wake_lo > wake_hi:
                raise InputValidationError('醒来区间', sleep['wake_range'], '开始不能晚于结束，醒来必须在所选日起床')
            duration = sleep.get('duration_range', [360, 540])
            if not isinstance(duration, (list, tuple)) or len(duration) != 2:
                raise InputValidationError('总睡眠分钟范围', duration, '需要“最小分钟-最大分钟”')
            minimum, maximum = number(duration, '总睡眠分钟范围', 240, 720, True)
            if bed_hi < bed_lo:
                bed_hi += 1440
            if bed_lo >= 720:
                bed_lo -= 1440
                bed_hi -= 1440
            if bed_lo + minimum > wake_hi or bed_hi + maximum < wake_lo:
                raise InputValidationError('睡眠区间组合', f"入睡{sleep['bedtime_range']}；醒来{sleep['wake_range']}；总分钟{duration}", '三个条件没有可同时满足的组合')
        else:
            raise InputValidationError('睡眠模式', sleep.get('mode'), '请选择整体提前或随机')
    if not sleep and not request.get('preset') and all(request.get(key) is None for key in TARGETS):
        raise InputValidationError('修改目标', '', '请至少填写一个三环／步数目标或勾选调整睡眠')
    return request


def validate_config(config):
    if not isinstance(config, dict):
        raise InputValidationError('私有配置', '', 'JSON顶层需要对象格式')
    for key in ['adb', 'serial', 'package', 'process', 'app_version', 'timezone', 'expected_emulator_model', 'expected_emulator_board']:
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise InputValidationError('私有配置.' + key, '', '此字段需要非空文本')
    if not re.fullmatch(r'emulator-\d+|127\.0\.0\.1:\d+', config['serial']):
        raise InputValidationError('私有配置.serial', '', '只支持配置的电脑模拟器，不能以实体手机作为注入目标')
    if config.get('emulator_verified') is not True:
        raise InputValidationError('私有配置.emulator_verified', '', '需要已核实的电脑模拟器配置')
    if config['package'] != 'com.huawei.health' or config['process'] != 'com.huawei.health:DaemonService' or config['app_version'] != '17.0.8.310':
        raise InputValidationError('私有配置.应用适配', '', '当前只支持已适配的原版运动健康及健康后台')
    port = config.get('frida_remote_port')
    if type(port) is not int or not 1024 <= port <= 65535:
        raise InputValidationError('私有配置.frida_remote_port', port, '端口需为1024–65535的整数')
    try:
        timezone_value(config['timezone'])
    except (ValueError, TypeError):
        raise InputValidationError('私有配置.timezone', config['timezone'], '时区应为带正负号的HH:MM，例如+08:00') from None
    date_input(config.get('date', ''))
