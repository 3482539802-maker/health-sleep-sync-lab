"""Single-date CLI. Every real input/output must be outside the public repository."""
import argparse
import copy
import json
from pathlib import Path
import sys
import time
import traceback
from .client import Client
from .model import by_minute,check,digest,make_plan,original_score,review,rows,validate,validate_plan,verify_grant

REPOSITORY=Path(__file__).resolve().parents[1]
def private_path(value):
    path=Path(value).resolve();check(not path.is_relative_to(REPOSITORY),'Real files must stay outside repository');return path
def load(path):return json.loads(path.read_text(encoding='utf-8'))
def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2)
def sibling(out,suffix):return out.with_name(out.name+suffix)
def successful(response):check(isinstance(response,dict) and response.get('resultCode')==0,'Native cloud call failed; stop and inspect private response')

def execute(args):
    if args.command=='review':
        return review(load(private_path(args.plan)))
    out=private_path(args.out);check(not out.exists(),'Existing output preserved')
    if args.command=='plan':
        backup_path=private_path(args.backup)
        result=make_plan(load(backup_path),args.target_start,args.target_end,args.reference_start)
        result['source_backup_sha256']=digest(backup_path);save(out,result)
        return {'plan_sha256':digest(out),**review(result)}
    config=load(private_path(args.config))
    if args.command=='backup':
        with Client(config) as client:
            segments=client.read();successful(segments)
            stats=client.stats();successful(stats)
        save(out,{'format_version':1,'config':config,'segments':segments,'stats':stats})
        return {'backup_saved_privately':True,'segment_count':len(rows(segments)),'writes_performed':False,'backup_sha256':digest(out)}
    plan_path=private_path(args.plan);plan=load(plan_path);target=validate_plan(plan)
    check(config==plan['config'],'Configuration differs from reviewed plan')
    grant=load(private_path(args.grant));verify_grant(plan,plan_path,grant,args.command)
    marker=sibling(out,'.started');check(not marker.exists(),'Previous mutation marker retained; do not retry automatically')
    with Client(config) as client:
        client.configure(source=str(target[0]['deviceCode']),target_start_ms=plan['target_start_ms'],target_end_ms=plan['target_end_ms'])
        current=client.read();successful(current);save(sibling(out,'.before-segments.json'),current)
        actual=rows(current);expected_target=by_minute(target)
        if args.command=='apply':
            expected=by_minute(actual)
            check(not actual or expected==expected_target,'Unexpected current-day cloud data; stop without deletion')
            save(marker,{'operation':'apply','date':config['date'],'plan_sha256':digest(plan_path)})
            batches=[]
            for index,offset in enumerate(range(0,len(target),200)):
                batch=target[offset:offset+200];response=client.upload(batch)
                save(sibling(out,f'.batch-{index}-response.json'),response);successful(response)
                expected.update(by_minute(batch));after=client.read();save(sibling(out,f'.batch-{index}-read.json'),after);successful(after)
                check(by_minute(rows(after))==expected,'Unexpected partial state; inspect private evidence and stop')
                batches.append({'batch':index,'uploaded_count':len(batch),'verified_count':len(expected)})
                save(sibling(out,f'.batch-{index}-verified.json'),batches)
            check(expected==expected_target,'Incomplete target')
            result={'cloud_target_verified':True,'count':len(target),'batches':batches,'phone_verification_pending':True}
        else:
            check(by_minute(actual)==expected_target,'Current segments do not match reviewed target')
            score=original_score(plan);client.configure(original_score=score)
            current_stats=client.stats();save(sibling(out,'.before-stats.json'),current_stats);successful(current_stats)
            totals=[t for t in (current_stats.get('professionalSleepTotal') or []) if int(t.get('recordDay',0))==int(config['date'].replace('-',''))]
            check(len(totals)==1,'Expected one current sleep summary')
            before=totals[0];sleep=before['professionalSleep']
            check(int(sleep['fallAsleepTime'])==plan['target_start_ms'] and int(sleep['wakeupTime'])==plan['target_end_ms'],'Current summary bounds differ')
            check(int(sleep['allSleepTime'])==len(target),'Current summary duration differs')
            restored=copy.deepcopy(before);restored['professionalSleep']['sleepScore']=score
            restored.update(dataSource=2,deviceCode=0,generateTime=int(time.time()*1000))
            save(sibling(out,'.restore-plan.json'),restored)
            if int(sleep.get('sleepScore',0))==score:
                result={'original_score_already_present':True,'writes_performed':False,'phone_verification_pending':True}
            else:
                save(marker,{'operation':'restore-original-score','date':config['date'],'plan_sha256':digest(plan_path)})
                response=client.restore_score(restored);save(sibling(out,'.restore-response.json'),response);successful(response)
                after=client.stats();save(sibling(out,'.after-stats.json'),after);successful(after)
                candidates=[t for t in (after.get('professionalSleepTotal') or []) if int(t.get('recordDay',0))==int(config['date'].replace('-','')) and int(t['professionalSleep'].get('sleepScore',0))==score]
                same=lambda s:{k:v for k,v in s.items() if k!='sleepScore'}
                check(len(candidates)==1 and same(candidates[0]['professionalSleep'])==same(sleep),'Unexpected summary change; stop')
                segments_after=client.read();save(sibling(out,'.after-segments.json'),segments_after);successful(segments_after)
                check(by_minute(rows(segments_after))==expected_target,'Segments changed during score restoration; inspect privately')
                result={'original_score_restored_in_cloud':True,'other_summary_fields_unchanged':True,'segments_unchanged':True,'not_recalculated':True,'phone_verification_pending':True}
    save(out,result);return result

def parser():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    backup=sub.add_parser('backup');backup.add_argument('--config',required=True);backup.add_argument('--out',required=True)
    plan=sub.add_parser('plan');plan.add_argument('--backup',required=True);plan.add_argument('--out',required=True)
    for flag in ['target-start','target-end','reference-start']:plan.add_argument('--'+flag,required=True)
    review_parser=sub.add_parser('review');review_parser.add_argument('--plan',required=True)
    for operation in ['apply','restore-score']:
        item=sub.add_parser(operation)
        for flag in ['config','plan','grant','out']:item.add_argument('--'+flag,required=True)
    return p

def main():
    args=parser().parse_args()
    try:print(json.dumps(execute(args),ensure_ascii=True))
    except Exception:
        # Detailed runtime exceptions may contain identifiers, paths or payloads.
        if getattr(args,'out',None):
            try:save(sibling(private_path(args.out),'.failure-private.json'),{'traceback_private':traceback.format_exc()})
            except Exception:pass
        print('Stopped: validation or runtime operation failed. Inspect private evidence; do not remove markers or retry writes blindly.',file=sys.stderr)
        return 1
    return 0
if __name__=='__main__':raise SystemExit(main())
