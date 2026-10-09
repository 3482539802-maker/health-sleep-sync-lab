"""All inputs are fabricated fixtures, independent of participant data."""
import copy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from sleep_sync_lab.model import by_minute,bounds,digest,make_plan,original_score,validate,validate_plan,verify_grant
from sleep_sync_lab.__main__ import private_path

CONFIG={'date':'2001-01-01','timezone':'+08:00'}
BASE=int(datetime.fromisoformat('2001-01-01T00:00:00+08:00').timestamp()*1000)
def sample(minute,stage='PROFESSIONAL_SLEEP_SHALLOW'):
    start=BASE+minute*60000
    return {'type':9,'deviceCode':'SYNTHETIC_SOURCE','recordId':'SYNTHETIC_ROW_'+str(minute),
      'startTime':str(start),'endTime':str(start+60000),'timeZone':'+08:00',
      'samplePoints':[{'key':stage,'startTime':str(start),'endTime':str(start+60000),'value':1}]}
def backup():
    return {'config':CONFIG,'segments':{'resultCode':0,'detailInfos':[sample(i) for i in range(2,8)]},
      'stats':{'resultCode':0,'professionalSleepTotal':[{'recordDay':20010101,'professionalSleep':{'sleepScore':72}}]}}
def plan():return make_plan(backup(),'2001-01-01T00:00:00+08:00','2001-01-01T00:06:00+08:00','2001-01-01T00:02:00+08:00')

class ModelTests(unittest.TestCase):
    def test_timezone_boundary_is_not_utc_midnight(self):
        self.assertEqual(bounds(CONFIG),(BASE,BASE+86400000))
    def test_gap_fill_preserves_retained_rows_and_references(self):
        p=plan();self.assertEqual(len(validate_plan(p)),6)
        self.assertEqual(p['synthetic_mapping'],[{'target_start_ms':BASE,'reference_start_ms':BASE+120000},{'target_start_ms':BASE+60000,'reference_start_ms':BASE+180000}])
        self.assertEqual(p['target'][2:],backup()['segments']['detailInfos'][:4])
    def test_truncated_reference_stops_before_writes(self):
        with self.assertRaises(ValueError):make_plan(backup(),'2001-01-01T00:00:00+08:00','2001-01-01T00:06:00+08:00','2001-01-01T00:07:00+08:00')
    def test_mixed_source_rejected(self):
        rows=[sample(1),sample(2)];rows[1]['deviceCode']='OTHER_SYNTHETIC_SOURCE'
        with self.assertRaises(ValueError):validate(rows)
    def test_duplicate_minute_rejected(self):
        with self.assertRaises(ValueError):validate([sample(1),sample(1)])
    def test_sample_point_bounds_must_match(self):
        r=sample(1);r['samplePoints'][0]['endTime']=str(BASE)
        with self.assertRaises(ValueError):validate([r])
    def test_reference_and_retained_changes_detected(self):
        for index in [0,3]:
            p=plan();p['target'][index]['samplePoints'][0]['key']='PROFESSIONAL_SLEEP_DEEP'
            with self.assertRaises(ValueError):validate_plan(p)
    def test_extra_synthetic_mapping_rejected(self):
        p=plan();p['synthetic_mapping'].append({'target_start_ms':BASE+300000,'reference_start_ms':BASE+120000})
        with self.assertRaises(ValueError):validate_plan(p)
    def test_failed_query_is_not_empty_data(self):
        b=backup();b['segments']['resultCode']=1004
        with self.assertRaises(ValueError):make_plan(b,'2001-01-01T00:00:00+08:00','2001-01-01T00:06:00+08:00','2001-01-01T00:02:00+08:00')
    def test_score_requires_original_backup(self):
        p=plan();self.assertEqual(original_score(p),72)
        p['backup']['stats']['professionalSleepTotal'][0]['professionalSleep']['sleepScore']=0
        with self.assertRaises(ValueError):original_score(p)
    def test_grant_requires_actual_flags_and_immutable_plan(self):
        p=plan()
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'plan.json';path.write_text(json.dumps(p),encoding='utf-8')
            g={'date':p['date'],'plan_sha256':digest(path),'allow_rebuild':True,'official_phone_day_deletion_verified':False}
            with self.assertRaises(ValueError):verify_grant(p,path,g,'apply')
            g['official_phone_day_deletion_verified']=True;verify_grant(p,path,g,'apply')
            path.write_text(json.dumps(p)+'\n',encoding='utf-8')
            with self.assertRaises(ValueError):verify_grant(p,path,g,'apply')
    def test_real_outputs_inside_repository_rejected(self):
        with self.assertRaises(ValueError):private_path(Path(__file__).parent/'real.json')
    def test_version_and_server_identity_do_not_change_content_comparison(self):
        r=sample(1);changed=copy.deepcopy(r);changed.update(recordId='SYNTHETIC_REASSIGNED',dataId='SYNTHETIC_DATA',version=2)
        self.assertEqual(by_minute([r]),by_minute([changed]))
if __name__=='__main__':unittest.main()
