# [Change Log] Date: 2026-10-19 | Author: Codex | Version: V2.995
import importlib.util, sys, types, unittest
from pathlib import Path
from unittest.mock import patch
from starlette.responses import JSONResponse
BACKEND=Path(__file__).resolve().parents[1]/"01_Current_Deliverables/app/backend"

def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod

class PairingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bq=module(BACKEND/"kernels/bom_quote.py","bom_pairing_kernel")
        core=types.ModuleType("core")
        core.CFG={"source":"kingdee"};core.JSONResponse=JSONResponse
        core.pull_token_ok=core._current_user=core._require_perm=lambda *a:None
        core.db=types.SimpleNamespace()
        kernels=types.ModuleType("kernels");kernels.bom_quote=cls.bq
        dt=types.ModuleType("kernels.dingtalk_bom");dt.set_iid_store=lambda *a:None
        with patch.dict(sys.modules,{"core":core,"kernels":kernels,"kernels.dingtalk_bom":dt}):
            cls.r=module(BACKEND/"routers/bom_quote.py","bom_pairing_route")
    def test_normalization_preserves_variant(self):
        q=self.bq
        self.assertEqual(q.canonical_cp(" cp07108602 （sn2） "),"CP07108602(SN2)")
        self.assertNotEqual(q.canonical_cp("CP07108602(SN2)"),q.canonical_cp("CP07108602(SN3)"))
    def test_all_fee_differences(self):
        a={"制造费用小计不含税":2,"装卸费不含税":.1,"管理费加成含税":.5}
        b={"制造费用小计不含税":3,"装卸费不含税":.2,"管理费加成含税":.7}
        d=self.bq.diff_goods([],[],a,b)
        self.assertEqual({x["field"] for x in d["rows"]},{"fee_mfg","fee_load","fee_adm"})
    def test_repeat_intake_skips_same_oa_even_when_cost_differs(self):
        r=self.r;rec={"productName":"速冻桃胶银耳制品","cpCode":"CP07108602（SN2）"}
        it={"idx":0,"rec":rec,"productKey":self.bq.product_key(rec),"comp":{},"checksOk":True,"stagedFile":"biz.xlsx"}
        db=types.SimpleNamespace(bom_list_entries=lambda *a:[{"id":191,"approval_no":"OA1","cp_code":"CP07108602(SN2)"}],audit=lambda *a,**k:None)
        with patch.object(r,"db",db):
            out=r._book_staged({"approvalNo":"OA1","records":[it]},"test",{0},{"name":"test"})
        self.assertEqual(len(out["skipped"]),1);self.assertEqual(out["booked"],[])
        self.assertIn("191",out["skipped"][0]["reason"])
    def test_other_oa_is_not_suppressed(self):
        r=self.r;rec={"productName":"a","cpCode":"CP07108602(SN2)"}
        it={"idx":0,"rec":rec,"productKey":self.bq.product_key(rec),"comp":{},"checksOk":True,"stagedFile":"x","numFp":"new"}
        def reached(*a):raise RuntimeError("new-record-path")
        db=types.SimpleNamespace(bom_list_entries=lambda *a:[{"id":191,"approval_no":"OA1","cp_code":"CP07108602(SN2)"}],bom_find_dup=reached)
        with patch.object(r,"db",db),self.assertRaisesRegex(RuntimeError,"new-record-path"):
            r._book_staged({"approvalNo":"OA2","records":[it]},"test",{0},{"name":"test"})

if __name__=="__main__":unittest.main()
