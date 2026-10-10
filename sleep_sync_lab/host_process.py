"""Bounded child processes suitable for a Windows GUI parent, with private diagnostics."""
import os
import subprocess
from .errors import AdbOperationError, AdbLaunchError


def silence_windows_crash_dialogs():
    # Return failures to our caller instead of blocking a GUI worker on an OS dialog.
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.SetErrorMode(kernel.GetErrorMode() | 0x0001 | 0x0002 | 0x8000)


def run_adb(executable, parts, serial=None, timeout=40):
    silence_windows_crash_dialogs()
    command = [str(executable)] + (['-s', serial] if serial else []) + list(parts)
    options = {'stdin': subprocess.DEVNULL, 'capture_output': True, 'timeout': timeout}
    if os.name == 'nt':
        # ADB needs no console. Repeated console creation from pythonw is unnecessary.
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(command, **options)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AdbLaunchError(list(parts), error) from error
    # pidof exits 1 for an absent process. Only that precise empty response is benign.
    absent_pid = list(parts)[:2] == ['shell', 'pidof'] and result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip()
    if result.returncode != 0 and not absent_pid:
        raise AdbOperationError(list(parts), result)
    return result
