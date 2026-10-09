// Original authored adapter; contains no APK code, identifiers or health examples.
let config=null;
function use(name){try{return Java.use(name);}catch(_){return Java.use('defpackage.'+name);}}
function nativeCall(action){return new Promise((resolve,reject)=>Java.perform(function(){
  try{resolve(action());}catch(error){reject(new Error(String(error)));}
}));}
function context(){
  const app=Java.use('android.app.ActivityThread').currentApplication();
  const T=use(config.transport_class),transport=T.b.overload('android.content.Context').call(T,app);
  const S=use(config.gson_factory_class),gson=S.d.overload().call(S);
  return {transport,gson,I:Java.use('java.lang.Integer'),L:Java.use('java.lang.Long')};
}
function encoded(gson,response){return response===null?null:JSON.parse(String(gson.toJson.overload('java.lang.Object').call(gson,response)));}
rpc.exports={
  configure(input){config=JSON.parse(input);return true;},
  read(){return nativeCall(()=>{
    const {transport,gson,I,L}=context();
    const R=Java.use('com.huawei.hwcloudmodel.healthdatacloud.model.GetHealthDataByTimeReq'),q=R.$new();
    q.setQueryType(I.valueOf(2));q.setDataType(I.valueOf(2));q.setType(I.valueOf(9));
    q.setStartTime(L.valueOf(config.day_start_ms));q.setEndTime(L.valueOf(config.day_end_ms));
    return encoded(gson,transport.d.overload('com.huawei.hwcloudmodel.healthdatacloud.model.GetHealthDataByTimeReq').call(transport,q));
  });},
  stats(){return nativeCall(()=>{
    const {transport,gson,I}=context();
    const R=Java.use('com.huawei.hwcloudmodel.healthdatacloud.model.GetHealthStatReq'),q=R.$new();
    q.setStartTime(config.record_day);q.setEndTime(config.record_day);q.setDataSource(2);q.setDeviceCode(0);
    const types=Java.use('java.util.HashSet').$new();types.add(I.valueOf(9));q.setTypes(types);
    return encoded(gson,transport.c.overload('com.huawei.hwcloudmodel.healthdatacloud.model.GetHealthStatReq').call(transport,q));
  });},
  upload(input){return nativeCall(()=>{
    const data=JSON.parse(input),allowed=['PROFESSIONAL_SLEEP_SHALLOW','PROFESSIONAL_SLEEP_DEEP','PROFESSIONAL_SLEEP_DREAM'];
    if(!data.length||data.length>200)throw new Error('Batch size guard');
    for(let i=0;i<data.length;i++){
      const r=data[i],point=r.samplePoints&&r.samplePoints[0],start=Number(r.startTime),end=Number(r.endTime);
      if(Number(r.type)!==9||String(r.deviceCode)!==String(config.source)||start<config.target_start_ms||end>config.target_end_ms||
         start%60000!==0||end-start!==60000||r.samplePoints.length!==1||!allowed.includes(point.key)||
         Number(point.startTime)!==start||Number(point.endTime)!==end||
         (i&&Number(data[i-1].endTime)!==start))throw new Error('Source/interval/stage guard');
    }
    const {transport,gson,L}=context(),Type=Java.use('com.huawei.hwcloudmodel.model.unite.HealthDetail');
    const list=Java.use('java.util.ArrayList').$new();
    for(const r of data)list.add(Java.cast(gson.fromJson.overload('java.lang.String','java.lang.Class').call(gson,JSON.stringify(r),Type.class),Type));
    const R=Java.use('com.huawei.hwcloudmodel.healthdatacloud.model.AddHealthDataReq'),req=R.$new();
    req.setDetailInfo(list);req.setTimeZone(data[0].timeZone);req.setLocalMaxVersion(L.valueOf(0));
    return encoded(gson,transport.e.overload('com.huawei.hwcloudmodel.healthdatacloud.model.AddHealthDataReq').call(transport,req));
  });},
  restoreScore(input){return nativeCall(()=>{
    const total=JSON.parse(input),sleep=total.professionalSleep;
    if(total.recordDay!==config.record_day||total.dataSource!==2||Number(total.deviceCode)!==0||
       !sleep||sleep.sleepScore!==config.original_score||sleep.sleepScore<1||sleep.sleepScore>100||
       Number(sleep.fallAsleepTime)!==config.target_start_ms||Number(sleep.wakeupTime)!==config.target_end_ms)
      throw new Error('Original score/day guard');
    const {transport,gson}=context(),Type=Java.use('com.huawei.hwcloudmodel.model.unite.ProfessionalSleepTotal');
    const native=Java.cast(gson.fromJson.overload('java.lang.String','java.lang.Class').call(gson,input,Type.class),Type);
    const list=Java.use('java.util.ArrayList').$new();list.add(native);
    const R=Java.use('com.huawei.hwcloudmodel.healthdatacloud.model.AddHealthStatReq'),req=R.$new();
    req.setProfessionalSleepTotal(list);req.setTimeZone(total.timeZone);
    return encoded(gson,transport.a.overload('com.huawei.hwcloudmodel.healthdatacloud.model.AddHealthStatReq').call(transport,req));
  });}
};
