// Read-only registered identity selection with artificial app/device managers.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let entries=[];
const config={package:'com.huawei.health'};
const clients={h:()=>({size:()=>entries.length,get:i=>i}),g:i=>({
 c:()=>entries[i].code,d:()=>i,g:()=>i
})};
const classes={
 kyz:{a:()=>({d:()=>7})},lbk:{c:()=>clients},
 lbc:{a:()=>({c:i=>({getPackageName:()=>entries[i].app||config.package})})},
 lox:{e:()=>1},lbt:{e:()=>({a:i=>({getDeviceUniqueCode:()=>entries[i].phone?'-1':'watch',
  getDeviceType:()=>entries[i].type||77})})}
};
const box={config,rpc:{exports:{}},nativeCall:fn=>Promise.resolve().then(fn),use:n=>classes[n],
 Java:{use:n=>n.endsWith('ProductMap')?{b:(_key,type)=>({size:()=>1,get:()=>({b:()=>Number(type)===77?'02A':'00E'})})}:{},cast:x=>x}};
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../sleep_sync_lab/automation_native.js'),'utf8'),box);
(async()=>{
 const get=box.rpc.exports.autoUploadSource;
 await assert.rejects(get(),/Registered upload source unavailable/);
 entries=[{code:'9007199254740993',phone:true},{code:'1234',phone:false}];
 assert.equal((await get()).deviceCode,'9007199254740993');
 config.upload_device_code='1234';assert.equal((await get()).deviceCode,'1234');
 config.upload_device_code='99';await assert.rejects(get(),/Registered upload source unavailable/);
 delete config.upload_device_code;
 entries=[{code:'0',phone:true},{code:'1234',app:'unrelated.app'}];
 await assert.rejects(get(),/Registered upload source unavailable/);
 entries=[{code:'1234'},{code:'5678'}];await assert.rejects(get(),/ambiguous/);
 entries=[{code:'1234'},{code:'1234'}];assert.equal((await get()).deviceCode,'1234');
 config.upload_require_sleep_source=true;
 entries=[{code:'9007199254740993',phone:true,type:1},{code:'1234',type:77},{code:'5678',type:32}];
 let result=await get();assert.equal(result.deviceCode,'9007199254740993');
 assert.equal(result.sleepSource.deviceCode,'1234');assert.equal(result.sleepSource.deviceCategory,42);
 assert.equal(result.sleepSource.sourceKind,'registered_wearable');
 config.sleep_upload_device_code='5678';await assert.rejects(get(),/Registered sleep device unavailable/);
 config.sleep_upload_device_code='9007199254740993';await assert.rejects(get(),/Registered sleep device unavailable/);
 delete config.sleep_upload_device_code;
 entries=[{code:'1',phone:true,type:1},{code:'2',type:32}];
 await assert.rejects(get(),/Registered sleep device unavailable/);
 entries=[{code:'1',phone:true,type:1},{code:'1234',type:77},{code:'5678',type:77}];
 await assert.rejects(get(),/Registered sleep device unavailable or ambiguous/);
 config.sleep_upload_device_code='5678';result=await get();assert.equal(result.sleepSource.deviceCode,'5678');
 config.upload_require_sleep_source=false;delete config.sleep_upload_device_code;
 entries=[{code:'1',phone:true,type:1}];assert.equal((await get()).deviceCode,'1');
 process.stdout.write('Read-only registered upload source selection passed\n');
})().catch(e=>{process.stderr.write(String(e));process.exitCode=1;});
