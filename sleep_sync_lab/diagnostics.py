"""Durable private incidents for failures before a job exists and GUI callbacks."""
from datetime import datetime
import traceback
from .automation import private, save_failure
from .audit import failure_details, runtime_manifest
from .errors import user_message


def record_incident(error, runs, phase, inputs=None, directory=None):
    message = user_message(error)
    target = private(directory) if directory else private(runs) / ('panel_error_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    packet = {'message': message, 'phase': phase, 'field': getattr(error, 'field', None)}
    try:
        target.mkdir(parents=True, exist_ok=True)
        save_failure(target, 'panel_failure_private.json', {'time': datetime.now().astimezone().isoformat(),
                     'phase': phase, 'message': message, 'inputs': inputs or {}, 'diagnostics': failure_details(error),
                     'traceback': ''.join(traceback.format_exception(type(error), error, error.__traceback__)),
                     'runtime': runtime_manifest()})
        packet['evidence'] = str(target)
    except Exception:
        packet['evidence'] = None
        packet['message'] += '\n失败记录未能保存，请检查备份目录的权限和剩余空间。'
    return packet
