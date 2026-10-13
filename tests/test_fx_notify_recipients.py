# [Change Log]
# Date: 2026-10-13 | Author: Claude Opus 5.5 | Version: V2.933（汇率录入·通知设置搜名字加人）
# Description: 汇率通知设置的钉钉收件人——保存 / 读回 / 花名册三个接口的后端核对。
#   不 import app；db 用临时 SQLite，钉钉一律打桩（不连真钉钉、不发任何通知）。
#   重点：选的人存 userid、手打的存手机号，名字只是显示用且只留名单里的人；老号码能借花名册认成人名；
#   口令不对不保存；手机号不是 11 位不保存；没权限进不来。
#   运行：repo 根目录 PYTHONPATH=01_Current_Deliverables/app/backend python -m unittest tests.test_fx_notify_recipients -v
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "01_Current_Deliverables" / "app" / "backend"
sys.path.insert(0, str(BACKEND))

_TMP = tempfile.TemporaryDirectory(prefix="fx-notify-", ignore_cleanup_errors=True)   # Windows 下 sqlite 文件退出时还被占着
os.environ.setdefault("DB_URL", "sqlite:///" + (Path(_TMP.name) / "fx.sqlite").as_posix())

import db                          # noqa: E402
import notifier                    # noqa: E402
from routers import fxrate as fxr  # noqa: E402

PEOPLE = [{"userid": "u001", "name": "张三", "title": "总账会计", "dept": "财务部"},
          {"userid": "u002", "name": "李四", "title": "出纳", "dept": "财务部"}]


class FxNotifyRecipientTests(unittest.TestCase):
    def setUp(self):
        db.set_setting("fx_notify_cfg", None, "test")
        db.set_setting("recipient_names", {}, "test")
        self.user = {"name": "测试员"}
        self._orig = (fxr._require_perm, fxr._fxrate_notify_passcode, notifier.load_dingtalk_conf,
                      notifier.dt_roster, notifier.dt_userids_by_mobiles)
        fxr._require_perm = lambda request, perm: self.user
        fxr._fxrate_notify_passcode = lambda: "pc"
        notifier.load_dingtalk_conf = lambda: {"appkey": "k", "appsecret": "s", "agentid": "1",
                                               "mobiles": ["13800000001"], "userids": []}
        notifier.dt_roster = lambda *a, **k: {"ok": True, "people": list(PEOPLE)}
        notifier.dt_userids_by_mobiles = lambda mobiles, conf=None: {m: "u001" for m in mobiles if m == "13800000001"}
        notifier._ROSTER_CACHE.update(ts=0.0, people=None)

    def tearDown(self):
        (fxr._require_perm, fxr._fxrate_notify_passcode, notifier.load_dingtalk_conf,
         notifier.dt_roster, notifier.dt_userids_by_mobiles) = self._orig

    def _save(self, **body):
        return fxr.fxrate_notify_config_save({"passcode": "pc", **body}, None)

    def test_picked_person_saved_as_userid_and_read_back_with_name(self):
        r = self._save(dt_mobiles=["13800000001"], dt_userids=["u002"],
                       dt_names={"u:u002": "李四", "13800000001": "张三", "u:gone": "不在名单里的人"})
        self.assertTrue(r["ok"])
        got = fxr.fxrate_notify_config(None)
        self.assertEqual(got["dt_mobiles"], ["13800000001"])
        self.assertEqual(got["dt_userids"], ["u002"])
        self.assertEqual(got["dt_names"], {"u:u002": "李四", "13800000001": "张三"})   # 名单外的名字不留
        dt, _sm, _ch = fxr._fxrate_effective_confs()                                   # 发送用的还是 userid / 手机号
        self.assertEqual((dt["mobiles"], dt["userids"]), (["13800000001"], ["u002"]))

    def test_names_fall_back_to_portal_recipient_names(self):
        db.set_setting("recipient_names", {"13800000001": "张三"}, "test")
        self.assertEqual(fxr.fxrate_notify_config(None)["dt_names"], {"13800000001": "张三"})

    def test_wrong_passcode_or_bad_mobile_not_saved(self):
        r = fxr.fxrate_notify_config_save({"passcode": "x", "dt_userids": ["u002"]}, None)
        self.assertEqual(r.status_code, 403)
        r = self._save(dt_mobiles=["1380000"], dt_userids=[])
        self.assertFalse(r["ok"])
        self.assertIn("11 位", r["msg"])
        self.assertIsNone(db.get_setting("fx_notify_cfg", None))

    def test_roster_returns_people_and_maps_old_mobiles(self):
        r = fxr.fxrate_notify_roster(None)
        self.assertTrue(r["ok"])
        self.assertEqual([p["name"] for p in r["people"]], ["张三", "李四"])
        self.assertEqual(r["mobile_uid"], {"13800000001": "u001"})
        self.assertNotIn("mobile", r["people"][0])                                     # 花名册不带手机号

    def test_roster_failure_passes_reason_through(self):
        notifier.dt_roster = lambda *a, **k: {"ok": False, "msg": "应用可能未开通「通讯录成员信息读取」"}
        r = fxr.fxrate_notify_roster(None)
        self.assertFalse(r["ok"])
        self.assertIn("通讯录", r["msg"])

    def test_no_permission_blocked(self):
        fxr._require_perm = lambda request, perm: None
        self.assertEqual(fxr.fxrate_notify_roster(None).status_code, 403)


if __name__ == "__main__":
    unittest.main()
