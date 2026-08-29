import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('sup', ROOT/'scripts'/'autonomous_supervisor.py')
sup=importlib.util.module_from_spec(spec);sys.modules[spec.name]=sup;spec.loader.exec_module(sup)
KST=timezone(timedelta(hours=9))
NOW=datetime(2026,8,29,22,0,0,tzinfo=KST)


def healthy():
    collector={'fast':{'state':'success','stage':'complete','lastSuccessAt':'2026-08-29T20:35:55+09:00','jobsCount':55733,'coverageEvidence':{'fresh':True,'currentComplete':True,'exactLinks':True,'supportCoverage':{'exactLinks':True},'sourceReconciliation':{'reconciledSources':38,'totalSources':38,'missingAfter':0,'missingOfficialIdsFromCurrentDataset':0,'centralPaginationComplete':True}}}}
    deep={'effectiveState':'success','effectiveAt':'2026-08-29T20:37:47+09:00','effectiveProof':'38-source-reconciliation','effectiveEvidence':{'currentComplete':True,'missingAfter':0,'missingOfficialIdsFromCurrentDataset':0,'centralPaginationComplete':True,'supportCoverageCurrentComplete':True}}
    quality={'generatedAt':'2026-08-29 21:52:26 KST','summary':{'exactStableIdDuplicateGroups':0,'exactUrlDuplicateGroups':0,'missingDetailLinks':0,'missingOfficialIds':0,'resultLikeNotices':53,'staleInactiveOver120Days':34631,'publishedToOfficialRatio':3.065}}
    return collector,deep,quality


class SupervisorTests(unittest.TestCase):
    def test_current_evidence_is_not_needlessly_repaired(self):
        c,d,q=healthy();p=sup.build_plan(c,d,q,{},NOW)
        self.assertIsNone(p['selectedAction'])
        self.assertEqual(p['health'],'degraded')
        self.assertTrue(any(x['code']=='overcollection-review-only' for x in p['observations']))

    def test_failed_fast_collector_dispatches_fast_refresh(self):
        c,d,q=healthy();c['fast']['state']='failed';c['fast']['stage']='verification'
        p=sup.build_plan(c,d,q,{},NOW)
        self.assertEqual(p['selectedAction']['workflow'],'update-jobs.yml')

    def test_missing_official_id_has_highest_recovery_priority(self):
        c,d,q=healthy();c['fast']['state']='failed';c['fast']['coverageEvidence']['sourceReconciliation']['missingOfficialIdsFromCurrentDataset']=2
        p=sup.build_plan(c,d,q,{},NOW)
        self.assertEqual(p['selectedAction']['workflow'],'recover-missing-jobs.yml')
        self.assertEqual(p['selectedAction']['code'],'coverage-integrity-failure')

    def test_stale_deep_audit_dispatches_deep_audit(self):
        c,d,q=healthy();d['effectiveAt']='2026-08-27T10:00:00+09:00'
        p=sup.build_plan(c,d,q,{},NOW)
        self.assertEqual(p['selectedAction']['workflow'],'daily-deep-audit.yml')

    def test_cooldown_blocks_repeat_dispatch(self):
        c,d,q=healthy();c['fast']['state']='failed'
        state={'dispatchHistory':{'update-jobs.yml':{'lastAttemptAt':'2026-08-29T21:00:00+09:00','count':1}}}
        p=sup.build_plan(c,d,q,state,NOW)
        self.assertIsNone(p['selectedAction'])
        self.assertTrue(p['blockedByCooldown'])

    def test_review_only_warnings_never_trigger_cleanup(self):
        c,d,q=healthy();q['summary']['resultLikeNotices']=500;q['summary']['staleInactiveOver120Days']=99999;q['summary']['publishedToOfficialRatio']=9.0
        p=sup.build_plan(c,d,q,{},NOW)
        self.assertIsNone(p['selectedAction'])
        self.assertFalse(p['policy']['destructiveCleanup'])

    def test_record_creates_cooldown_state(self):
        c,d,q=healthy();c['fast']['state']='failed';p=sup.build_plan(c,d,q,{},NOW)
        st=sup.record_dispatch({},p,'update-jobs.yml','dispatched','ok',NOW)
        self.assertEqual(st['dispatchHistory']['update-jobs.yml']['lastResult'],'dispatched')
        self.assertEqual(st['dispatchHistory']['update-jobs.yml']['count'],1)

if __name__=='__main__': unittest.main()
