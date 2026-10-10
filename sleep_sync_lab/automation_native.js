// Authored adapter for one selected day. Uses the signed app's own models.
// No credentials, participant records, APK sources or machine paths are included.
let autoPermit=null,autoBlocked={},autoGuarded=[];
function autoCtx(){return {...context(),app:Java.use('android.app.ActivityThread').currentApplication()};}
function autoRun(action){return nativeCall(action);}
function autoPost(name,req,q){
 const {transport,gson}=autoCtx();
 autoPermit={thread:String(Java.use('java.lang.Thread').currentThread().getId()),req,
   body:String(gson.toJson.overload('java.lang.Object').call(gson,q))};
 try{return encoded(gson,transport[name].overload('com.huawei.hwcloudmodel.healthdatacloud.model.'+req).call(transport,q));}
 finally{autoPermit=null;}
}
function autoHealth(type,start,end){
 const {transport,gson,I,L}=autoCtx(),C=Java.use('com.huawei.hwcloudmodel.healthdatacloud.model.GetHealthDataByTimeReq'),q=C.$new();
 q.setQueryType(I.valueOf(2));q.setDataType(I.valueOf(2));q.setType(I.valueOf(type));
 q.setStartTime(L.valueOf(start));q.setEndTime(L.valueOf(end));
 return encoded(gson,transport.d.overload('com.huawei.hwcloudmodel.healthdatacloud.model.GetHealthDataByTimeReq').call(transport,q));
}
Object.assign(rpc.exports,{
 autoGuard(){return autoRun(()=>{
  const {transport,gson}=autoCtx(),T=use(config.transport_class);
  for(const [name,req,rsp] of [
   ['e','AddHealthDataReq','AddHealthDataRsp'],['a','AddHealthStatReq','AddHealthStatRsp'],
   ['c','AddSportDataReq','AddSportDataRsp'],['c','AddSportTotalReq','AddSportTotalRsp'],
   ['b','DeleteHealthDataReq','DelHealthDataRsp'],['b','DeleteHealthStatReq','DeleteHealthStatRsp'],
   ['a','DeleteDataByTimeReq','DelHealthDataRsp']]){
   const method=T[name].overload('com.huawei.hwcloudmodel.healthdatacloud.model.'+req),C=Java.use('com.huawei.hwcloudmodel.model.unite.'+rsp);
   method.implementation=function(q){
    if(autoPermit&&autoPermit.req===req&&autoPermit.thread===String(Java.use('java.lang.Thread').currentThread().getId())&&autoPermit.body===String(gson.toJson.overload('java.lang.Object').call(gson,q)))return method.call(this,q);
    autoBlocked[req]=(autoBlocked[req]||0)+1;
    return Java.cast(gson.fromJson.overload('java.lang.String','java.lang.Class').call(gson,'{"resultCode":1001,"resultDesc":"Automatic PC write paused"}',C.class),C);
   };autoGuarded.push(req);
  }
  const X=use('oxj');let networks=0;
  for(const name of ['a','d','e'])for(const ov of X[name].overloads){
   const args=ov.argumentTypes.map(x=>x.className);
   if(args[0]!=='java.lang.String'||args[1]!=='java.util.Map'||args[3]!=='java.lang.Class')continue;
   networks++;
   ov.implementation=function(...args){
    const path=String(args[0]).match(/\/dataSync\/[^?#]+/i),thread=String(Java.use('java.lang.Thread').currentThread().getId());
    if(path&&!/\/(get|query|download|check)[^/]*$/i.test(path[0])&&!(autoPermit&&autoPermit.thread===thread)){
     autoBlocked[path[0]]=(autoBlocked[path[0]]||0)+1;
     if(ov.returnType.className==='void')return;
     return gson.fromJson.overload('java.lang.String','java.lang.Class').call(gson,'{"resultCode":1001,"resultDesc":"Automatic PC write paused"}',args[3]);
    }
    return ov.call(this,...args);
   };
  }
  if(networks<2)throw new Error('Network guard adapter incomplete');
  return {transportMethods:autoGuarded.length,networkMethods:networks};
 });},
 autoGuardState(){return {blocked:autoBlocked};},
 autoSnapshot(){return autoRun(()=>{
  const {transport,gson,I,L}=autoCtx(),types=Java.use('java.util.HashSet').$new();
  for(const n of [1,2,3,4,5,10,14])types.add(I.valueOf(n));
  const R=Java.use('com.huawei.hwcloudmodel.healthdatacloud.model.GetSportDataByTimeReq'),q=R.$new();
  q.setQueryType(I.valueOf(2));q.setDataType(I.valueOf(2));q.setSportTypes(types);
  q.setStartTime(L.valueOf(config.day_start_ms));q.setEndTime(L.valueOf(config.day_end_ms-1));
  const sport=encoded(gson,transport.a.overload('com.huawei.hwcloudmodel.healthdatacloud.model.GetSportDataByTimeReq').call(transport,q));
  const S=Java.use('com.huawei.hwcloudmodel.healthdatacloud.model.GetSportStatReq'),s=S.$new();
  s.setStartTime(I.valueOf(config.record_day));s.setEndTime(I.valueOf(config.record_day));s.setDataSource(I.valueOf(2));s.setDeviceCode(L.valueOf(0));
  const stats=encoded(gson,transport.b.overload('com.huawei.hwcloudmodel.healthdatacloud.model.GetSportStatReq').call(transport,s));
  return {sport,sport_stats:stats,intensity:autoHealth(12,config.day_start_ms,config.day_end_ms-1),
   active:autoHealth(200005,config.day_start_ms,config.day_end_ms-1),
   sleep:autoHealth(9,config.sleep_query_start_ms||config.day_start_ms,config.sleep_query_end_ms||config.day_end_ms-1)};
 });},
 autoHealthRead(type,start,end){return autoRun(()=>autoHealth(type,start,end));},
 autoLocalSleep(){return autoRun(()=>new Promise((resolve,reject)=>{
  let db=null;
  Java.choose('net.zetetic.database.sqlcipher.SQLiteDatabase',{
   onMatch(x){if(db===null&&x.isOpen()&&String(x.getPath()).endsWith('/hihealth_003.db'))db=Java.retain(x);},
   onComplete(){try{
    if(db===null)throw new Error('Original local database unavailable');
    const sql='SELECT start_time,end_time,type_id,client_id,sync_status,merged FROM sample_session_core WHERE start_time>='+config.sleep_query_start_ms+' AND start_time<='+config.sleep_query_end_ms+' AND sync_status<>2 AND type_id BETWEEN 22100 AND 22199 ORDER BY start_time';
    const c=db.rawQuery.overload('java.lang.String','[Ljava.lang.String;').call(db,sql,Java.array('java.lang.String',[])),intervals=[];
    try{while(c.moveToNext())intervals.push({start:Number(c.getLong(0)),end:Number(c.getLong(1)),type:Number(c.getInt(2)),client:Number(c.getInt(3)),sync:Number(c.getInt(4)),merged:Number(c.getInt(5))});}finally{c.close();}
    resolve({resultCode:0,intervals});
   }catch(e){reject(new Error(String(e)));}finally{if(db)db.$dispose();}}
  });
 }));},
 autoUploadHealth(input){return autoRun(()=>{
  const data=JSON.parse(input);if(!data.length||data.length>200)throw new Error('Health batch limit');
  const {gson,L}=autoCtx(),C=Java.use('com.huawei.hwcloudmodel.model.unite.HealthDetail'),list=Java.use('java.util.ArrayList').$new();
  for(const r of data){
   if(![9,12,200005].includes(Number(r.type)))throw new Error('Unsupported health type');
   const start=Number(r.startTime),end=Number(r.endTime);
   const scope=config.write_scope;if(!scope||start<scope.start||end>scope.end||end<=start)throw new Error('Health interval outside reviewed plan');
   if(!r.samplePoints||!r.samplePoints.length)throw new Error('Missing health samples');
   list.add(gson.fromJson.overload('java.lang.String','java.lang.Class').call(gson,JSON.stringify(r),C.class));
  }
  const R=Java.use('com.huawei.hwcloudmodel.healthdatacloud.model.AddHealthDataReq'),q=R.$new();
  q.setDetailInfo(list);q.setTimeZone(data[0].timeZone);q.setLocalMaxVersion(L.valueOf(0));return autoPost('e','AddHealthDataReq',q);
 });},
 autoUploadSport(input){return autoRun(()=>{
  const data=JSON.parse(input);if(!data.length||data.length>30)throw new Error('Sport batch limit');
  const {gson}=autoCtx(),C=Java.use('com.huawei.hwcloudmodel.model.unite.SportDetail'),list=Java.use('java.util.ArrayList').$new();
  for(const r of data){if(Number(r.startTime)<config.day_start_ms||Number(r.endTime)>config.day_end_ms||Number(r.endTime)-Number(r.startTime)!==60000)throw new Error('Sport day scope');list.add(gson.fromJson.overload('java.lang.String','java.lang.Class').call(gson,JSON.stringify(r),C.class));}
  const R=Java.use('com.huawei.hwcloudmodel.healthdatacloud.model.AddSportDataReq'),q=R.$new();q.setDetailInfo(list);q.setTimeZone(data[0].timeZone);return autoPost('c','AddSportDataReq',q);
 });},
 autoUploadSportSummary(input){return autoRun(()=>{
  const r=JSON.parse(input);if(r.recordDay!==config.record_day||r.dataSource!==2||r.sportType!==0||Number(r.deviceCode)!==0)throw new Error('Summary day/source scope');
  const {gson}=autoCtx(),C=Java.use('com.huawei.hwcloudmodel.model.unite.SportTotal'),list=Java.use('java.util.ArrayList').$new();
  list.add(gson.fromJson.overload('java.lang.String','java.lang.Class').call(gson,input,C.class));
  const R=Java.use('com.huawei.hwcloudmodel.healthdatacloud.model.AddSportTotalReq'),q=R.$new();q.setTotalInfo(list);q.setTimeZone(r.timeZone);q.setIsForce(0);return autoPost('c','AddSportTotalReq',q);
 });},
 autoUploadSleepSummary(input){return autoRun(()=>{
  const r=JSON.parse(input);if(r.recordDay!==config.record_day||r.dataSource!==2||Number(r.deviceCode)!==0)throw new Error('Sleep summary scope');
  const {gson}=autoCtx(),C=Java.use('com.huawei.hwcloudmodel.model.unite.ProfessionalSleepTotal'),list=Java.use('java.util.ArrayList').$new();list.add(gson.fromJson.overload('java.lang.String','java.lang.Class').call(gson,input,C.class));
  const R=Java.use('com.huawei.hwcloudmodel.healthdatacloud.model.AddHealthStatReq'),q=R.$new();q.setProfessionalSleepTotal(list);q.setTimeZone(r.timeZone);return autoPost('a','AddHealthStatReq',q);
 });},
 autoDeleteSleep(start,end){return autoRun(()=>{
  if(!config.delete_scope||start!==config.delete_scope.start||end!==config.delete_scope.end)throw new Error('Sleep deletion outside reviewed scope');
  const {gson,I,L}=autoCtx(),C=Java.use('com.huawei.hwcloudmodel.model.unite.DataTimeDelCondition'),condition=C.$new();
  condition.setType(I.valueOf(9));condition.setStartTime(L.valueOf(start));condition.setEndTime(L.valueOf(end));condition.setDeviceCode(L.valueOf(0));
  const list=Java.use('java.util.ArrayList').$new();list.add(condition);
  const R=Java.use('com.huawei.hwcloudmodel.healthdatacloud.model.DeleteDataByTimeReq'),q=R.$new();q.setDelDayDataConditons(list);
  return autoPost('a','DeleteDataByTimeReq',q);
 });},
 autoClearLocalSleep(start,end){return autoRun(()=>{
  if(!config.delete_scope||start!==config.delete_scope.start||end!==config.delete_scope.end)throw new Error('Local sleep deletion scope');
  const {app}=autoCtx(),U=use('kyz'),user=U.a().d(),B=use('lbk'),clients=B.c().h(user),K=use('lkx'),store=K.a.overload('android.content.Context').call(K,app);
  const types=Java.array('int',Array.from({length:100},(_,i)=>22100+i));
  return {localCleared:store.a.overload('long','long','[I','java.util.List','int').call(store,start,end,types,clients,user),phoneLocalCleared:false};
 });}
});
