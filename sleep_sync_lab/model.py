"""Offline validation and planning. No network, phone operations or cloud writes."""
import copy
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json

STAGES=frozenset({'PROFESSIONAL_SLEEP_SHALLOW','PROFESSIONAL_SLEEP_DEEP','PROFESSIONAL_SLEEP_DREAM'})

def check(condition,message):
    if not condition:raise ValueError(message)

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def timezone_value(value):
    check(isinstance(value,str) and len(value)==6 and value[0] in '+-' and value[3]==':','Timezone must be signed HH:MM')
    hours,minutes=int(value[1:3]),int(value[4:6]);check(hours<=14 and minutes<60 and (hours<14 or minutes==0),'Invalid timezone')
    return timezone((1 if value[0]=='+' else -1)*timedelta(hours=hours,minutes=minutes))

def bounds(config):
    day=date.fromisoformat(config['date']);start=datetime.combine(day,time(),timezone_value(config['timezone']))
    return int(start.timestamp()*1000),int((start+timedelta(days=1)).timestamp()*1000)

def iso_ms(value):
    parsed=datetime.fromisoformat(value);check(parsed.tzinfo is not None,'Explicit UTC offset required')
    result=int(parsed.timestamp()*1000);check(result%60000==0,'Whole-minute boundary required');return result

def rows(response):
    check(isinstance(response,dict) and response.get('resultCode')==0,'Query did not succeed')
    if 'detailInfos' in response:
        check(isinstance(response['detailInfos'],list),'Invalid detailInfos');return response['detailInfos']
    check(isinstance(response.get('data',{}),dict),'Invalid data map')
    return [row for group in response.get('data',{}).values() for row in group]

def validate(records,start=None,end=None,continuous=True,allow_wake=False):
    check(isinstance(records,list) and bool(records),'No records')
    ordered=sorted(records,key=lambda r:int(r['startTime']));source=str(ordered[0].get('deviceCode',''))
    check(source not in ['', '0','None'],'Missing source')
    seen=set()
    for record in ordered:
        begin,finish=int(record['startTime']),int(record['endTime'])
        check(int(record['type'])==9 and str(record['deviceCode'])==source,'Mixed type/source')
        check(begin%60000==0 and finish-begin==60000,'Only whole one-minute rows supported')
        check(begin not in seen,'Duplicate minute');seen.add(begin)
        points=record.get('samplePoints');check(isinstance(points,list) and len(points)==1,'Exactly one sample point required')
        point=points[0];check(point['key'] in STAGES or (allow_wake and point['key']=='PROFESSIONAL_SLEEP_WAKE'),'Unsupported stage: current planner supports shallow/deep/REM only')
        check(int(point['startTime'])==begin and int(point['endTime'])==finish,'Sample point bounds differ')
    if continuous:check(all(int(a['endTime'])==int(b['startTime']) for a,b in zip(ordered,ordered[1:])),'Gap in target')
    if start is not None:check(int(ordered[0]['startTime'])==start,'Unexpected target start')
    if end is not None:check(int(ordered[-1]['endTime'])==end,'Unexpected target end')
    return ordered

def content(record):
    return json.dumps({k:v for k,v in record.items() if k not in ['recordId','dataId','version']},sort_keys=True,separators=(',',':'))

def by_minute(records):
    result={int(r['startTime']):content(r) for r in records};check(len(result)==len(records),'Repeated minute');return result

def make_plan(backup,target_start,target_end,reference_start):
    config=backup['config'];day_start,day_end=bounds(config)
    start,end,reference=map(iso_ms,[target_start,target_end,reference_start])
    check(day_start<=start<end<=day_end,'Target outside selected day; cross-day plans need separate validation')
    original=validate(rows(backup['segments']),continuous=False)
    check(all(day_start<=int(r['startTime'])<int(r['endTime'])<=day_end for r in original),'Backup includes other days')
    lookup={int(r['startTime']):r for r in original};target=[];synthetic=[];next_reference=reference
    for minute in range(start,end,60000):
        if minute in lookup:target.append(copy.deepcopy(lookup[minute]));continue
        check(next_reference in lookup,'Reference sequence too short or missing')
        source=lookup[next_reference];record=copy.deepcopy(source)
        for obj in [record,record['samplePoints'][0]]:
            # Match the original wire representation of long timestamps.
            obj['startTime']=str(minute) if isinstance(obj['startTime'],str) else minute
            obj['endTime']=str(minute+60000) if isinstance(obj['endTime'],str) else minute+60000
        target.append(record);synthetic.append({'target_start_ms':minute,'reference_start_ms':next_reference})
        next_reference+=60000
    validate(target,start,end)
    return {'format_version':1,'date':config['date'],'config':copy.deepcopy(config),'backup':copy.deepcopy(backup),
      'target_start_ms':start,'target_end_ms':end,'target':target,'synthetic_mapping':synthetic,
      'synthetic_data_not_measured':bool(synthetic),'original_source_minutes_outside_target':sum(not(start<=m<end) for m in lookup)}

def validate_plan(plan):
    check(plan.get('format_version')==1 and plan['date']==plan['config']['date'],'Plan format/date mismatch')
    records=validate(plan['target'],int(plan['target_start_ms']),int(plan['target_end_ms']))
    original=validate(rows(plan['backup']['segments']),continuous=False)
    check(str(records[0]['deviceCode'])==str(original[0]['deviceCode']),'Plan source differs from backup')
    check(plan['backup']['config']==plan['config'],'Plan backup config differs')
    left,right=bounds(plan['config']);check(left<=int(records[0]['startTime'])<int(records[-1]['endTime'])<=right,'Plan outside selected day')
    original_by_time={int(r['startTime']):r for r in original}
    check(len(original_by_time)==len(original),'Backup duplicates')
    mappings={int(m['target_start_ms']):int(m['reference_start_ms']) for m in plan['synthetic_mapping']}
    check(len(mappings)==len(plan['synthetic_mapping']),'Repeated synthetic mapping')
    expected_synthetic=set()
    for r in records:
        minute=int(r['startTime'])
        if minute in original_by_time:check(content(r)==content(original_by_time[minute]),'Retained original content changed')
        else:
            expected_synthetic.add(minute);check(minute in mappings and mappings[minute] in original_by_time,'Synthetic minute lacks reference')
            ref=copy.deepcopy(original_by_time[mappings[minute]])
            for obj in [ref,ref['samplePoints'][0]]:
                obj['startTime']=str(minute) if isinstance(obj['startTime'],str) else minute
                obj['endTime']=str(minute+60000) if isinstance(obj['endTime'],str) else minute+60000
            check(content(ref)==content(r),'Synthetic reference differs')
    check(set(mappings)==expected_synthetic,'Synthetic mappings do not match target')
    return records

def original_score(plan):
    cloud=plan['backup']['stats'];check(cloud.get('resultCode')==0,'No successful original summary backup')
    day=int(plan['date'].replace('-',''))
    totals=[t for t in (cloud.get('professionalSleepTotal') or []) if int(t['recordDay'])==day]
    scores={int(t['professionalSleep']['sleepScore']) for t in totals if int(t['professionalSleep'].get('sleepScore',0))>0}
    check(len(scores)==1,'No unique original measured score in backup')
    score=scores.pop();check(1<=score<=100,'Invalid original score');return score

def verify_grant(plan,path,grant,operation):
    check(grant.get('date')==plan['date'] and grant.get('plan_sha256')==digest(path),'Grant does not match reviewed plan')
    if operation=='apply':
        check(grant.get('allow_rebuild') is True and grant.get('official_phone_day_deletion_verified') is True,'Specific authorization and phone deletion confirmation required')
    elif operation=='restore-score':check(grant.get('allow_restore_original_score') is True,'Original-score restoration authorization required')
    else:raise ValueError('Unknown operation')

def review(plan):
    records=validate_plan(plan)
    try:score_present=original_score(plan)>0
    except ValueError:score_present=False
    return {'date':plan['date'],'target_minutes':len(records),'synthetic_minutes':len(plan['synthetic_mapping']),
      'original_minutes_outside_target':plan['original_source_minutes_outside_target'],'original_score_backup_present':score_present,
      'writes_performed':False,'private_output':True}
