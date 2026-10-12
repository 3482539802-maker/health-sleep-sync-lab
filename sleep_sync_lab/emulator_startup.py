"""Start one managed LDPlayer instance; park its health package across reboots."""
import json
import os
from pathlib import Path
import re
import subprocess
import time

from .errors import PanelOperationError, AdbOperationError, AdbLaunchError
from .host_process import run_adb, silence_windows_crash_dialogs


def profile_path():
    return Path.home() / '.health_sleep_sync_lab' / 'emulator_startup_private.json'


def load_profile(config):
    # Runtime machine settings are separate from immutable health plans, including old plans.
    profile = config.get('emulator_startup')
    if profile is None:
        path = profile_path()
        if not path.exists():
            return None
        try:
            profiles = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(profiles, dict):
                raise ValueError('Startup profiles must be keyed by serial')
            profile = profiles.get(config['serial'])
        except (OSError, ValueError) as error:
            raise PanelOperationError('模拟器自动启动配置无法读取，尚未写入。请检查本机启动配置；不需要重新生成计划。') from error
    if profile is None:
        return None
    if not isinstance(profile, dict):
        raise PanelOperationError('模拟器自动启动配置格式无效，尚未写入。')
    if profile.get('enabled') is False:
        return None
    return ManagedEmulator(config, profile)


def connection_state(config):
    result = run_adb(config['adb'], ['devices'], timeout=10)
    for line in result.stdout.decode('utf-8', 'replace').splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == config['serial']:
            return parts[1]
    return None


class ManagedEmulator:
    def __init__(self, config, profile):
        self.config, self.profile = config, dict(profile)
        if any(not isinstance(profile.get(key), str) or not profile[key].strip()
               for key in ['console', 'instance_name', 'state_dir']):
            raise PanelOperationError('模拟器自动启动配置缺少控制程序、实例名称或私有记录目录，尚未写入。')
        index = profile.get('instance_index')
        if (type(index) is not int or not 0 <= index <= 100 or
                config['serial'] not in {f'emulator-{5554 + index * 2}', f'127.0.0.1:{5555 + index * 2}'} or
                config.get('emulator_verified') is not True or config['package'] != 'com.huawei.health'):
            raise PanelOperationError('模拟器自动启动实例与已核实的ADB目标不匹配，尚未写入。请检查本机启动配置。')
        self.index, self.console = index, Path(profile['console'])
        self.name = profile.get('instance_name')
        self.timeout = profile.get('timeout_seconds', 180)
        if not self.name or type(self.timeout) is not int or not 10 <= self.timeout <= 600:
            raise PanelOperationError('模拟器自动启动名称或等待时间无效，尚未写入。')
        if not self.console.is_file():
            raise PanelOperationError('找不到配置的模拟器控制程序，请检查本机启动配置中的 console 路径，尚未写入。')
        self.state_dir = Path(profile['state_dir'])
        from .automation import private
        private(self.state_dir)
        self.parked = self.state_dir / 'emulator_parked_private.json'
        self.lock = self.state_dir / 'emulator_startup.lock'
        self.lock_owned = False
        self.cold_started = False
        self.enabled_by_session = False
        self.trace = []
        self.log = lambda message: None

    def record(self, phase, **details):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        from .audit import event
        event(self.state_dir, phase, serial=self.config['serial'], instance_index=self.index, **details)
        self.trace.append(phase)

    def command(self, *args):
        silence_windows_crash_dialogs()
        command = [str(self.console), *args]
        options = dict(stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
        if os.name == 'nt':
            options['creationflags'] = subprocess.CREATE_NO_WINDOW
        try:
            result = subprocess.run(command, **options)
        except (OSError, subprocess.TimeoutExpired) as cause:
            error = PanelOperationError('模拟器启动命令失败或超时，尚未写入。请查看私有启动记录；原计划可保留。')
            error.command, error.timeout = command, getattr(cause, 'timeout', None)
            raise error from cause
        if result.returncode:
            error = PanelOperationError('模拟器启动程序返回失败，尚未写入。请查看私有启动记录。')
            error.command, error.returncode = command, result.returncode
            error.stdout, error.stderr = result.stdout, result.stderr
            raise error
        self.record('emulator_command', command=command, returncode=result.returncode,
                    stdout=result.stdout.decode('utf-8', 'replace'), stderr=result.stderr.decode('utf-8', 'replace'))
        return result.stdout.decode('utf-8', 'replace').strip()

    def acquire(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        try:
            with self.lock.open('x', encoding='utf-8') as handle:
                handle.write(str(os.getpid()))
        except FileExistsError as cause:
            raise PanelOperationError('另一个模拟器启动／连接任务正在运行，或上次异常留下锁。请检查任务状态，尚未写入。') from cause
        self.lock_owned = True

    def release(self):
        if self.lock_owned:
            self.lock.unlink()
            self.lock_owned = False

    def instance(self):
        rows = self.command('list2').splitlines()
        matching = [row.split(',') for row in rows if row.split(',')[0] == str(self.index)]
        if len(matching) != 1 or len(matching[0]) < 7 or matching[0][1] != self.name:
            raise PanelOperationError('找不到配置的研究模拟器实例，或实例名称已改变，尚未写入。不会启动其他实例。')
        return matching[0]

    def parked_proof(self):
        try:
            data = json.loads(self.parked.read_text(encoding='utf-8'))
            return (data.get('parked') is True and data.get('serial') == self.config['serial'] and
                    data.get('package') == self.config['package'] and data.get('instance_index') == self.index)
        except (OSError, ValueError):
            return False

    def ensure_ready(self):
        self.acquire()
        try:
            row = self.instance()
            status = connection_state(self.config)
            if status == 'unauthorized':
                raise PanelOperationError('配置模拟器ADB尚未授权，尚未写入。请完成该实例的调试授权。')
            running = int(row[5]) > 0 or int(row[6]) > 0
            if not running:
                if status == 'device':
                    raise PanelOperationError('ADB目标在线，但配置实例显示未运行，已停止。请检查目标是否被其他模拟器占用，尚未写入。')
                if not self.parked_proof():
                    raise PanelOperationError('模拟器尚未配置开机隔离，自动启动已停止，尚未写入。请先完成一次研究实例的自动启动设置。')
                self.log('模拟器未打开，正在启动已配置的研究实例…')
                self.record('emulator_launch_begin')
                # launch opens Android only; never launchex/runapp before UID protection.
                self.command('launch', '--index', str(self.index))
                self.cold_started = True
            else:
                self.log('正在等待已配置模拟器的ADB和系统就绪…')
            deadline = time.monotonic() + self.timeout
            last_error = None
            while time.monotonic() < deadline:
                status = connection_state(self.config)
                if status == 'unauthorized':
                    raise PanelOperationError('配置模拟器ADB尚未授权，尚未写入。请完成该实例的调试授权。')
                if status == 'device':
                    try:
                        remaining = max(.1, min(5, deadline - time.monotonic()))
                        result = run_adb(self.config['adb'], ['shell', 'getprop', 'sys.boot_completed'],
                                         self.config['serial'], timeout=remaining)
                        if result.stdout.strip() == b'1':
                            self.record('emulator_boot_ready', launched=self.cold_started)
                            self.log('模拟器已就绪，正在检查环境并建立上传保护…')
                            return
                    except AdbLaunchError as error:
                        if error.timeout is None:
                            raise
                        last_error = error
                    except AdbOperationError as error:
                        if (error.returncode & 0xffffffff) >= 0x80000000:
                            raise
                        last_error = error
                time.sleep(.5)
            raise PanelOperationError(f'模拟器在{self.timeout}秒内未启动就绪，尚未写入。请查看私有启动记录；不需要重新生成计划。') from last_error
        except BaseException as error:
            from .audit import failure_details
            try:
                self.record('emulator_startup_failed', diagnostics=failure_details(error))
            finally:
                self.release()
            raise

    def is_disabled(self, client):
        packages = client.adb('shell', 'pm', 'list', 'packages', '-d', '--user', '0').splitlines()
        return 'package:' + self.config['package'] in packages

    def activate(self, client):
        # Called after hardware/UID/root validation and both verified firewall rules.
        if not client.quarantine_confirmed:
            raise PanelOperationError('模拟器健康应用隔离未确认，已停止启动应用，尚未写入。')
        if self.cold_started and not self.is_disabled(client):
            raise PanelOperationError('开机隔离状态与保存记录不一致，已停止，尚未写入。请检查研究实例是否被手动改动。')
        self.record('emulator_uid_protected')
        # Invalidate proof before enabling; a crash cannot authorize an unprotected reboot.
        self.parked.unlink(missing_ok=True)
        self.enabled_by_session = True
        client.adb('shell', 'pm', 'enable', '--user', '0', self.config['package'])
        if self.is_disabled(client):
            raise PanelOperationError('研究实例的运动健康未能启用，尚未写入。')
        self.ensure_frida(client)
        self.record('emulator_app_enabled')

    def ensure_frida(self, client):
        if client.adb('shell', 'pidof', 'gt2-frida-server'):
            return
        expected = self.profile.get('frida_server_sha256')
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise PanelOperationError('模拟器重启后Frida未运行，启动配置缺少已核实的服务程序校验值，尚未写入。')
        result = client.adb('shell', "su -c 'sha256sum /data/local/tmp/gt2-frida-server'")
        if not result.split() or result.split()[0] != expected:
            raise PanelOperationError('模拟器服务程序校验未通过，尚未写入。不会安装或下载替代程序。')
        command = ['shell', "su -c '/data/local/tmp/gt2-frida-server -l 127.0.0.1:27042 -D >/data/local/tmp/gt2-frida-server.log 2>&1'"]
        try:
            run_adb(self.config['adb'], command, self.config['serial'], timeout=8)
        except AdbLaunchError as error:
            # Some emulator su wrappers keep the shell open after daemonization.
            # Inspect the running server; never issue a second start command.
            if error.timeout is None or not client.adb('shell', 'pidof', 'gt2-frida-server'):
                raise
        for _ in range(20):
            if client.adb('shell', 'pidof', 'gt2-frida-server'):
                return
            time.sleep(.25)
        raise PanelOperationError('模拟器服务程序未就绪，尚未写入。请查看私有启动记录。')

    def park(self, client):
        # Package state is in this instance's data; no shared system-image changes.
        client.adb('shell', 'pm', 'disable-user', '--user', '0', self.config['package'])
        if not self.is_disabled(client) or not client.quarantine_confirmed:
            raise PanelOperationError('研究实例开机隔离保存失败。请查看私有记录，暂勿重启实例。')
        self.record('emulator_app_parked')
        data = {'parked': True, 'serial': self.config['serial'], 'package': self.config['package'],
                'instance_index': self.index, 'uid': client.uid}
        temporary = self.parked.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.parked)


def configure(config, profile):
    """One-time setup on an already running, verified research emulator, without health RPCs."""
    from .automation import private
    from .automation_client import AutomationClient
    manager = ManagedEmulator(config, profile)
    client = AutomationClient(config)
    manager.acquire()
    try:
        row = manager.instance()
        if int(row[5]) <= 0 and int(row[6]) <= 0:
            raise PanelOperationError('首次自动启动设置需要研究实例已打开；尚未建立开机隔离，不会直接启动。')
        client.verify_adb()
        client.validate_environment()
        client.emulator = manager
        client.adb('shell', 'am', 'force-stop', config['package'])
        client.set_quarantine(True)
        checksum = client.adb('shell', "su -c 'sha256sum /data/local/tmp/gt2-frida-server'").split()
        if not checksum or not re.fullmatch(r'[0-9a-f]{64}', checksum[0]):
            raise PanelOperationError('未找到已安装的研究服务程序，首次设置未完成。不会下载或安装替代程序。')
        manager.profile['frida_server_sha256'] = checksum[0]
        manager.park(client)
        path = private(profile_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        profiles = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if path.exists():
            from datetime import datetime
            backup = manager.state_dir / ('startup_profile_before_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_private.json')
            with backup.open('xb') as handle:
                handle.write(path.read_bytes())
        profiles[config['serial']] = manager.profile
        temporary = path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as handle:
            json.dump(profiles, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        manager.record('emulator_profile_configured')
        return path
    finally:
        if client.validated:
            client.__exit__()
        else:
            manager.release()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='配置研究模拟器自动启动；不上传或删除健康数据。')
    parser.add_argument('--config', required=True, help='仓库外私有健康连接配置')
    parser.add_argument('--console', required=True, help='ldconsole.exe完整路径')
    parser.add_argument('--index', required=True, type=int, help='明确的研究实例序号')
    parser.add_argument('--name', required=True, help='研究实例名称，用于核验')
    parser.add_argument('--state-dir', required=True, help='仓库外私有启动记录目录')
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    from .automation import load, private
    config = load(private(args.config))
    configure(config, dict(enabled=True, console=args.console, instance_index=args.index,
                           instance_name=args.name, state_dir=args.state_dir, timeout_seconds=args.timeout))
    print('自动启动已配置。执行时若实例未打开会自动启动；生成计划仍可离线。')


if __name__ == '__main__':
    main()
