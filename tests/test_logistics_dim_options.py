import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "01_Current_Deliverables" / "app" / "backend"))
from routers import logistics_review as LR


class LogisticsDimOptionsTest(unittest.TestCase):
    def setUp(self):
        LR._DIMOPT_CACHE.clear()
        LR._perm = lambda request: {"name": "test"}

    def tearDown(self):
        LR._DIMOPT_CACHE.clear()

    def test_rnd_expense_account_is_included(self):
        calls = []

        def query(_session, _conf, form, _fields, _filter):
            calls.append((form, _filter))
            if form == "BD_Account":
                return [{"c": "6604", "n": "研发费用"}]
            return []

        LR.kc.login = lambda: (object(), object())
        LR.kc._query = query
        result = LR.review_dim_options(None)
        self.assertIn("6604", dict(calls)["BD_Account"])
        self.assertIn({"code": "6604", "name": "研发费用"}, result["acct"])


if __name__ == "__main__":
    unittest.main()
