"""Attach only to an isolated emulator. No token extraction or phone deletion."""
from importlib.resources import files
import json
from pathlib import Path
import re
import subprocess
from .model import bounds,check

class Client:
    def __init__(self,config):
        self.config=dict(config);self.session=None;self.forward_created=False
        start,end=bounds(config)
        self.config.update(day_start_ms=start,day_end_ms=end,record_day=int(config['date'].replace('-','')))
    def adb(self,*parts):
        result=subprocess.run([self.config['adb'],'-s',self.config['serial'],*parts],capture_output=True,timeout=40)
        check(result.returncode==0,'ADB operation failed; inspect privately')
        return result.stdout.decode('utf-8','replace').strip()
    def __enter__(self):
        import frida
        check(re.fullmatch(r'emulator-\d+|127\.0\.0\.1:\d+',self.config['serial']) is not None,'Physical phone is not a supported injection target')
        if self.adb('shell','getprop','ro.kernel.qemu')!='1':
            # Some PC emulators hide qemu and spoof mobile hardware properties.
            check(self.config.get('emulator_verified') is True,'Explicit PC emulator verification required when qemu flag is absent')
            check(self.adb('shell','getprop','ro.product.cpu.abi') in ['x86','x86_64'],'Expected PC emulator CPU')
            for key,prop in [('expected_emulator_model','ro.product.model'),('expected_emulator_board','ro.product.board')]:
                check(bool(self.config.get(key)) and self.adb('shell','getprop',prop)==self.config[key],'Confirmed emulator profile differs')
        check(self.config['package']=='com.huawei.health' and self.config['process']=='com.huawei.health:DaemonService','Original app/daemon required')
        check(self.config['app_version']=='17.0.8.310','Unknown adapter version')
        package=self.adb('shell','dumpsys','package',self.config['package'])
        check('versionName='+self.config['app_version'] in package,'Installed version differs')
        pid=self.adb('shell','pidof',self.config['process']);check(pid.isdigit(),'Expected one running original daemon')
        port=int(self.config['frida_remote_port']);check(1024<=port<=65535,'Invalid local port')
        local='tcp:'+str(port)
        check(not any(local in line.split()[1:2] for line in self.adb('forward','--list').splitlines()),'Existing forward retained; choose a free port')
        try:
            self.adb('forward',local,'tcp:27042');self.forward_created=True
            self.session=frida.get_device_manager().add_remote_device('127.0.0.1:'+str(port)).attach(int(pid))
            source=files('frida_tools').joinpath('bridges/java.js').read_text(encoding='utf-8')+'\n;globalThis.Java=bridge;\n'
            source+=Path(__file__).with_name('native.js').read_text(encoding='utf-8')
            self.script=self.session.create_script(source);self.script.load()
            self.script.exports_sync.configure(json.dumps(self.config));return self
        except BaseException:
            self.__exit__(None,None,None);raise
    def configure(self,**updates):
        self.config.update(updates);self.script.exports_sync.configure(json.dumps(self.config))
    def read(self):return self.script.exports_sync.read()
    def stats(self):return self.script.exports_sync.stats()
    def upload(self,records):return self.script.exports_sync.upload(json.dumps(records))
    def restore_score(self,total):return self.script.exports_sync.restore_score(json.dumps(total))
    def __exit__(self,*exc):
        try:
            if self.session:self.session.detach()
        finally:
            if self.forward_created:self.adb('forward','--remove','tcp:'+str(self.config['frida_remote_port']))
