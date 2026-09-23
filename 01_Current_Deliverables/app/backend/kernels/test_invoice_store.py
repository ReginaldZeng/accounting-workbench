# [Change Log]
# Date: 2026-09-24 | Author: Claude / c | Version: V-draft（发票管家）
# Description: 发票管家数据层单测——内存 SQLite 跑全部增删改查（全部合成数据，不碰真实票据）＋ MySQL 方言 DDL 编译检查。
#   审查修复回归：拉附件中断次数上限、金额拒 NaN/超界、JSON 不落 NaN、半截/失败文件不挡重传、台账含作废与拆分合计、
#   留痕按票并入、批量取后补单/票、用过的配对码可查。
"""invoice_store 单测：不 import db/core，不连任何外部服务。"""
import ast
import re
import unittest
import warnings
from sqlalchemy import create_engine, update
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable, CreateIndex
from kernels import invoice_store as s

TODAY = "2026-09-24"


class Base(unittest.TestCase):
    def setUp(self):
        warnings.filterwarnings("ignore", message=".*Decimal objects natively.*")
        self.e = create_engine("sqlite://")
        s.md.create_all(self.e)

    def tearDown(self):
        self.e.dispose()

    def appr(self, inst="INST-1", **kw):
        f = dict(instId=inst, businessId="202609241030000001", template="付款申请（公对公）", title="测试付款",
                 applicant="申请人甲", applicantUid="u001", dept="测试部", company="测试公司甲",
                 amount="1,234.50", payeeName="供应商乙", payeeBank="测试银行", payeeAccount="6222000000000000",
                 reason="采购样品", erpNo="ERP-001", approvalStatus="COMPLETED", approvalResult="agree",
                 form=[{"name": "付款总额", "type": "MoneyField", "value": "1234.50"}])
        f.update(kw)
        return f

    def item(self, folder_id, number, **kw):
        cols = dict(folder_id=folder_id, kind="invoice", inv_type="normal", number=number,
                    dup_key=s.dup_key_of(number), issue_date="2026-09-01", seller_name="销方丙",
                    seller_tax_id="91000000TEST00001X", amount=100, tax=6, total=106, created_by="录入员")
        cols.update(kw)
        return s.item_insert(self.e, **cols)


class SchemaTests(unittest.TestCase):
    def test_eleven_tables_on_own_metadata(self):
        names = sorted(t.name for t in s.TABLES)
        self.assertEqual(len(names), 11)
        self.assertTrue(all(n.startswith("inv_") for n in names))
        self.assertIs(s.TABLES[0].metadata, s.md)

    def test_no_db_or_core_import(self):
        with open(s.__file__, encoding="utf-8") as fh:
            src = fh.read()
        mods = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        self.assertFalse(mods & {"db", "core", "kernels"}, mods)

    def test_mysql_ddl(self):
        d = mysql.dialect()
        for t in s.TABLES:
            ddl = str(CreateTable(t).compile(dialect=d))
            self.assertIsNone(re.search(r"VARCHAR(?!\()", ddl), t.name + " 有不写长度的 VARCHAR")
            self.assertNotIn(" TEXT", ddl.replace("LONGTEXT", ""), t.name + " 有裸 TEXT")
            for c in t.c:
                if c.name in s.JSON_DEFAULTS or c.name in s.TEXT_COLS:
                    self.assertRegex(ddl, r"`?%s`? LONGTEXT" % c.name, t.name + "." + c.name)
                if c.name in ("amount", "tax", "total", "pay_amount", "expect_amount", "alloc"):
                    self.assertRegex(ddl, r"`?%s`? NUMERIC\(18, 2\)" % c.name)
            if t.name == "inv_folder":
                self.assertRegex(ddl, r"`?attach_tries`? INTEGER")
            if t.name == "inv_item":
                self.assertRegex(ddl, r"`?dup_at`? VARCHAR\(26\)")
            if t.name == "inv_pair":
                self.assertRegex(ddl, r"`?qr_hash`? VARCHAR\(64\)")
            # 唯一约束/索引命名规范 + 索引长度（utf8mb4 × 4 字节 ≤ 3072）
            for con in t.constraints:
                if con.name and not con.name.startswith("pk"):
                    self.assertTrue(con.name.startswith("uq_inv_"), con.name)
            names = {ix.name for ix in t.indexes}
            if t.name == "inv_folder":
                self.assertTrue({"ix_inv_folder_created", "ix_inv_folder_opened"} <= names)
            if t.name == "inv_pair":
                self.assertIn("ix_inv_pair_qr", names)
            for ix in t.indexes:
                self.assertTrue(ix.name.startswith("ix_inv_"), ix.name)
                str(CreateIndex(ix).compile(dialect=d))
                size = sum((getattr(c.type, "length", None) or 4) * 4 for c in ix.columns)
                self.assertLess(size, 3072, ix.name)


class HelperTests(Base):
    def test_dup_key_and_money(self):
        self.assertEqual(s.dup_key_of("12345678901234567890"), "N:12345678901234567890")
        self.assertEqual(s.dup_key_of(" 12345678 ", "044001900111"), "C:044001900111:12345678")
        self.assertEqual(s.dup_key_of("12345678"), "")
        self.assertEqual(s.dup_key_of(""), "")
        self.assertEqual(str(s.money("1,234.565")), "1234.57")
        self.assertIsNone(s.money(""))
        with self.assertRaises(ValueError):
            s.money("abc")
        # NaN/无穷大/超出 Numeric(18,2)：一律当格式不对（MariaDB 写不进、JSON 回包 500）
        for bad in ("nan", "NaN", float("nan"), "inf", "-Infinity", float("inf"), "1e16", 1e17, "-1e16"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                s.money(bad)
        self.assertEqual(str(s.money("9999999999999999.99")), "9999999999999999.99")

    def test_json_never_stores_nan(self):
        iid = s.item_insert(self.e, folder_id=1, kind="invoice",
                            lines_json=[{"name": "x", "qty": float("nan"), "price": float("inf"), "amount": 1.5}])
        it = s.item_get(self.e, iid)
        self.assertEqual(it["lines_json"], [{"name": "x", "qty": None, "price": None, "amount": 1.5}])

    def test_prep_rejects_unknown_and_truncates(self):
        with self.assertRaises(KeyError):
            s.item_insert(self.e, nope=1)
        iid = s.item_insert(self.e, folder_id=1, kind="other", remark="长" * 600, number=12345)
        it = s.item_get(self.e, iid)
        self.assertEqual(len(it["remark"]), 500)
        self.assertEqual(it["number"], "12345")
        self.assertEqual(s.now_s()[4], "-")
        self.assertEqual(len(s.now_s()), 19)

    def test_row_json_defaults_and_money_float(self):
        iid = s.item_insert(self.e, folder_id=1, kind="invoice", amount="99.999",
                            lines_json=[{"name": "服务费", "amount": 10}], flags_json={"dup": {"kind": "item"}})
        it = s.item_get(self.e, iid)
        self.assertEqual(it["amount"], 100.0)
        self.assertIsInstance(it["amount"], float)
        self.assertEqual(it["lines_json"][0]["name"], "服务费")
        self.assertEqual(it["flags_json"]["dup"]["kind"], "item")
        self.assertEqual(it["pending_json"], [])
        self.assertEqual(it["field_src_json"], {})
        self.assertEqual(it["review"], "draft")
        self.assertEqual(it["status"], "active")
        self.assertIsNone(s.item_get(self.e, 999))
        # 坏 JSON 原样给出，不抛
        with self.e.begin() as cx:
            cx.execute(update(s.ITEM).where(s.ITEM.c.id == iid).values(pending_json="not-json"))
        self.assertEqual(s.item_get(self.e, iid)["pending_json"], "not-json")


class FolderTests(Base):
    def test_upsert_idempotent_and_keeps_status(self):
        fid = s.folder_upsert_from_approval(self.e, self.appr(), "录入员")
        f = s.folder_get(self.e, fid)
        self.assertEqual(f["status"], "collecting")
        self.assertEqual(f["source"], "scan")
        self.assertEqual(f["amount"], 1234.5)
        self.assertEqual(f["form_json"][0]["name"], "付款总额")
        self.assertEqual(f["reason"], "采购样品")
        s.folder_update(self.e, fid, status="submitted", reviewed_by="会计", review_note="备注")
        fid2 = s.folder_upsert_from_approval(self.e, self.appr(title="改了标题", approvalStatus="RUNNING"), "别人")
        self.assertEqual(fid, fid2)
        f = s.folder_get(self.e, fid)
        self.assertEqual(f["title"], "改了标题")
        self.assertEqual(f["approval_status"], "RUNNING")
        self.assertEqual(f["status"], "submitted")
        self.assertEqual(f["reviewed_by"], "会计")
        self.assertEqual(f["created_by"], "录入员")
        # 只给部分键：没给的不清空
        s.folder_upsert_from_approval(self.e, {"instId": "INST-1", "approvalResult": "refuse"}, "x")
        f = s.folder_get(self.e, fid)
        self.assertEqual(f["payee_name"], "供应商乙")
        self.assertEqual(f["approval_result"], "refuse")
        self.assertEqual(s.folder_by_inst(self.e, "INST-1")["id"], fid)
        self.assertIsNone(s.folder_by_inst(self.e, "none"))
        self.assertEqual(s.folder_by_business(self.e, "202609241030000001")["id"], fid)
        with self.assertRaises(ValueError):
            s.folder_upsert_from_approval(self.e, {}, "x")

    def test_manual_folders_and_recent(self):
        m1 = s.folder_create_manual(self.e, "无审批单票夹", "50", "测试公司甲", "甲")
        m2 = s.folder_create_manual(self.e, "", None, None, "甲")
        self.assertIsNone(s.folder_get(self.e, m1)["inst_id"])  # 多个 NULL 不撞唯一约束
        self.assertEqual(s.folder_get(self.e, m2)["title"], "手工票夹")
        other = s.folder_upsert_from_approval(self.e, self.appr("INST-9"), "乙")
        s.folder_update(self.e, m1, status="approved")
        self.assertEqual([r["id"] for r in s.folders_recent(self.e, "甲")], [m2, m1])
        s.desk_set(self.e, "甲", other)   # 打开别人的票夹 → 进我的最近
        recent = [r["id"] for r in s.folders_recent(self.e, "甲")]
        self.assertEqual(recent[-1], m1)  # 已审核的排最后
        self.assertIn(other, recent)
        self.assertEqual(len(s.folders_recent(self.e, "甲", limit=1)), 1)
        self.assertEqual(s.folders_recent(self.e, ""), [])

    def test_folder_items_soft_removed(self):
        fid = s.folder_create_manual(self.e, "t", 0, "", "甲")
        a = self.item(fid, "11111111111111111111")
        b = self.item(fid, "22222222222222222222")
        s.item_update(self.e, b, status="removed")
        self.assertEqual([i["id"] for i in s.folder_items(self.e, fid)], [a])
        self.assertEqual(len(s.folder_items(self.e, fid, include_removed=True)), 2)
        self.assertEqual([i["id"] for i in s.items_of_folders(self.e, [fid, 999])[fid]], [a])
        self.assertEqual(s.items_of_folders(self.e, []), {})


class FileTests(Base):
    def test_file_crud_and_lookups(self):
        fid = s.folder_create_manual(self.e, "t", 0, "", "甲")
        f1 = s.file_insert(self.e, folder_id=fid, role="original", origin="upload", name="a.pdf", sha256="h1",
                           orig_path="2026-09/1/1_o.pdf", pages=2)
        f2 = s.file_insert(self.e, folder_id=fid, role="attachment", origin="attachment", name="b.jpg",
                           sha256="h2", dt_file_id="dt-1")
        self.assertEqual(s.file_get(self.e, f1)["pages"], 2)
        self.assertEqual(s.file_get(self.e, f1)["status"], "active")
        self.assertTrue(s.file_update(self.e, f1, rotation=90, item_id=5))
        self.assertEqual(s.file_get(self.e, f1)["rotation"], 90)
        self.assertEqual(s.file_by_sha(self.e, fid, "h1")["id"], f1)
        self.assertIsNone(s.file_by_sha(self.e, fid + 1, "h1"))
        s.file_update(self.e, f2, status="removed")
        self.assertIsNone(s.file_by_sha(self.e, fid, "h2"))           # 移除后可重新放入
        self.assertEqual(s.file_by_dt(self.e, fid, "dt-1")["id"], f2)  # 但钉钉附件刷新不复活
        self.assertEqual([x["id"] for x in s.files_of_folder(self.e, fid)], [f1])
        self.assertEqual(len(s.files_of_folder(self.e, fid, include_removed=True)), 2)
        self.assertIsNone(s.file_by_dt(self.e, fid, ""))

    def test_failed_or_orphan_files_do_not_block_retry(self):
        """登记半路失败/落盘失败留下的文件行：不挡同一份文件重传、不挡钉钉附件重拉；"正在拉"占位要能查到（跳过坏附件用）。"""
        fid = s.folder_create_manual(self.e, "t", 0, "", "甲")
        orphan = s.file_insert(self.e, folder_id=fid, role="original", origin="attachment", sha256="h9", dt_file_id="dt-9")
        self.assertIsNone(s.file_by_sha(self.e, fid, "h9"))       # 没挂上票：重传能重新登记
        self.assertIsNone(s.file_by_dt(self.e, fid, "dt-9"))      # 没挂上票：刷新能重拉
        s.file_update(self.e, orphan, item_id=3)
        self.assertEqual(s.file_by_sha(self.e, fid, "h9")["id"], orphan)
        self.assertEqual(s.file_by_dt(self.e, fid, "dt-9")["id"], orphan)
        failed = s.file_insert(self.e, folder_id=fid, role="original", origin="upload", sha256="h8", dt_file_id="dt-8",
                               status="failed", item_id=None)
        self.assertIsNone(s.file_by_sha(self.e, fid, "h8"))
        self.assertIsNone(s.file_by_dt(self.e, fid, "dt-8"))
        self.assertNotIn(failed, [x["id"] for x in s.files_of_folder(self.e, fid)])
        mark = s.file_insert(self.e, folder_id=fid, role="marker", origin="attachment", status="pulling", dt_file_id="dt-7")
        self.assertEqual(s.file_by_dt(self.e, fid, "dt-7")["status"], "pulling")
        self.assertTrue(s.file_delete(self.e, mark))
        self.assertIsNone(s.file_by_dt(self.e, fid, "dt-7"))
        self.assertIsNone(s.file_get(self.e, mark))


class ItemTests(Base):
    def test_dup_lookup_excludes_void_and_removed(self):
        f1 = s.folder_upsert_from_approval(self.e, self.appr("I1"), "甲")
        f2 = s.folder_upsert_from_approval(self.e, self.appr("I2", businessId="B2", applicant="申请人乙"), "甲")
        n = "33333333333333333333"
        a = self.item(f1, n)
        b = self.item(f2, n)
        c = self.item(f2, n)
        hits = s.items_by_dup(self.e, "N:" + n, exclude_id=b)
        self.assertEqual([h["id"] for h in hits], [a, c])
        self.assertEqual(hits[0]["folder"]["business_id"], "202609241030000001")
        self.assertEqual(hits[1]["folder"]["applicant"], "申请人乙")
        self.assertNotIn("f__id", hits[0])
        s.item_update(self.e, a, review="void", void_by="经理")
        s.item_update(self.e, c, status="removed")
        self.assertEqual(s.items_by_dup(self.e, "N:" + n, exclude_id=b), [])
        self.assertEqual([h["id"] for h in s.items_by_dup(self.e, "N:" + n)], [b])
        self.assertEqual(s.items_by_dup(self.e, ""), [])
        orphan = s.item_insert(self.e, dup_key="N:9", kind="invoice")
        self.assertIsNone(s.items_by_dup(self.e, "N:9")[0]["folder"])
        self.assertEqual(s.items_by_dup(self.e, "N:9")[0]["id"], orphan)

    def test_proc_queue_claim_and_reset(self):
        a = s.item_insert(self.e, folder_id=1, proc_status="pending")
        b = s.item_insert(self.e, folder_id=1, proc_status="pending")
        c = s.item_insert(self.e, folder_id=1, proc_status="pending", status="removed")
        s.item_insert(self.e, folder_id=1)  # 默认 done
        self.assertEqual([i["id"] for i in s.items_pending_proc(self.e)], [a, b])
        self.assertEqual(len(s.items_pending_proc(self.e, limit=1)), 1)
        self.assertTrue(s.item_claim(self.e, a))
        self.assertFalse(s.item_claim(self.e, a))
        self.assertEqual(s.item_get(self.e, a)["proc_status"], "running")
        self.assertEqual(s.item_get(self.e, a)["proc_tries"], 1)
        fid = s.folder_create_manual(self.e, "t", 0, "", "甲")
        s.folder_update(self.e, fid, attach_status="pending")
        self.assertEqual([f["id"] for f in s.folders_pending_attach(self.e)], [fid])
        self.assertTrue(s.folder_claim_attach(self.e, fid))
        self.assertFalse(s.folder_claim_attach(self.e, fid))
        self.assertEqual(s.reset_running(self.e), {"items": 1, "folders": 1, "gaveUp": 0})
        self.assertEqual(s.item_get(self.e, a)["proc_status"], "pending")
        self.assertEqual(s.folder_get(self.e, fid)["attach_status"], "pending")
        self.assertEqual(s.item_get(self.e, c)["proc_status"], "pending")

    def test_attach_tries_give_up(self):
        """拉附件时进程被杀（坏附件）→ 重启续跑；被中断满 ATTACH_MAX_TRIES 次就标失败，不再死循环。"""
        fid = s.folder_create_manual(self.e, "t", 0, "", "甲")
        s.folder_update(self.e, fid, attach_status="pending")
        for n in range(1, s.ATTACH_MAX_TRIES):
            self.assertTrue(s.folder_claim_attach(self.e, fid))
            self.assertEqual(s.folder_get(self.e, fid)["attach_tries"], n)
            self.assertEqual(s.reset_running(self.e)["folders"], 1)        # 还没到上限：退回 pending 续跑
            self.assertEqual(s.folder_get(self.e, fid)["attach_status"], "pending")
        self.assertTrue(s.folder_claim_attach(self.e, fid))
        r = s.reset_running(self.e)
        self.assertEqual((r["folders"], r["gaveUp"]), (0, 1))
        f = s.folder_get(self.e, fid)
        self.assertEqual(f["attach_status"], "failed")
        self.assertIn("中断", f["attach_msg"])
        self.assertEqual(s.folders_pending_attach(self.e), [])

    def test_items_get_batch(self):
        a = s.item_insert(self.e, folder_id=1)
        b = s.item_insert(self.e, folder_id=2)
        got = s.items_get(self.e, [a, b, b, None, 999])
        self.assertEqual(sorted(got), [a, b])
        self.assertEqual(s.items_get(self.e, []), {})

    def test_items_of_later(self):
        a = s.item_insert(self.e, folder_id=1, later_id=7)
        b = s.item_insert(self.e, folder_id=1, later_id=7, status="removed")
        s.item_insert(self.e, folder_id=1, later_id=8)
        self.assertEqual([i["id"] for i in s.items_of_later(self.e, 7)], [a])
        self.assertEqual([i["id"] for i in s.items_of_later(self.e, 7, include_removed=True)], [a, b])

    def test_item_update_touches_updated_at(self):
        a = s.item_insert(self.e, folder_id=1, updated_at="2000-01-01 00:00:00")
        s.item_update(self.e, a, verify="green")
        self.assertNotEqual(s.item_get(self.e, a)["updated_at"], "2000-01-01 00:00:00")
        s.item_update(self.e, a, updated_at="2001-01-01 00:00:00")
        self.assertEqual(s.item_get(self.e, a)["updated_at"], "2001-01-01 00:00:00")


class OpeningLogDeskPairTests(Base):
    def test_opening(self):
        bid = s.batch_insert(self.e, "opening", "期初.xlsx", 4, {"ok": 3}, "甲")
        rows = [dict(number="44444444444444444444", total="10.5", seller_name="销方丙"),
                dict(number="12345678", code="044001900111", total=1),
                dict(number="44444444444444444444"),        # 本批重复
                dict(number="short")]                       # 算不出查重键
        self.assertEqual(s.opening_insert_many(self.e, rows, bid, "票总管", "甲"), 2)
        self.assertEqual(s.opening_insert_many(self.e, rows, bid, "票总管", "甲"), 0)  # 已在库
        m = s.opening_match(self.e, "N:44444444444444444444")
        self.assertEqual(m["total"], 10.5)
        self.assertEqual(m["source"], "票总管")
        self.assertIsNotNone(s.opening_match(self.e, "C:044001900111:12345678"))
        self.assertIsNone(s.opening_match(self.e, ""))
        st = s.opening_stats(self.e)
        self.assertEqual(st["total"], 2)
        self.assertEqual(st["batches"][0]["rows"], 2)
        self.assertEqual(st["batches"][0]["name"], "期初.xlsx")
        self.assertEqual(s.opening_insert_many(self.e, [], bid, "", "甲"), 0)

    def test_logs(self):
        s.log_add(self.e, "甲", "扫审批单", folder_id=1, detail={"code": "x"})
        s.log_add(self.e, "甲", "动" * 60, folder_id=1, item_id=5, detail="文字")
        s.log_add(self.e, "乙", "后补登记", later_id=3)
        logs = s.logs_of(self.e, folder_id=1)
        self.assertEqual(len(logs), 2)
        self.assertEqual(len(logs[0]["action"]), 40)       # 最新在前 + 截断
        self.assertEqual(logs[0]["detail"], "文字")
        self.assertEqual(logs[1]["detail"], {"code": "x"})
        self.assertEqual(len(s.logs_of(self.e, later_id=3)), 1)
        self.assertEqual(len(s.logs_of(self.e, item_id=5)), 1)
        self.assertEqual(len(s.logs_of(self.e, folder_id=1, later_id=3)), 3)
        self.assertEqual(len(s.logs_of(self.e, limit=1)), 1)
        self.assertIsNone(s.logs_of(self.e, later_id=3)[0]["detail"])
        # 票夹详情并上逐张票自己的留痕（哪怕那条没记票夹号）
        s.log_add(self.e, "丙", "税局对账", item_id=5, detail={"verify": "green"})
        self.assertEqual(len(s.logs_of(self.e, folder_id=1)), 2)
        self.assertEqual([x["action"] for x in s.logs_of(self.e, folder_id=1, item_ids=[5])][:1], ["税局对账"])
        self.assertEqual(len(s.logs_of(self.e, folder_id=1, item_ids=[5])), 3)

    def test_desk(self):
        self.assertIsNone(s.desk_get(self.e, "甲"))
        fid = s.folder_create_manual(self.e, "t", 0, "", "乙")
        s.desk_set(self.e, "甲", fid)
        self.assertEqual(s.desk_get(self.e, "甲"), fid)
        self.assertEqual(s.folder_get(self.e, fid)["opened_by"], "甲")
        s.desk_set(self.e, "甲", None)
        self.assertIsNone(s.desk_get(self.e, "甲"))
        s.desk_set(self.e, "甲", fid)
        self.assertEqual(s.desk_get(self.e, "甲"), fid)

    def test_pair_revoke_and_active(self):
        now = "2026-09-24 10:00:00"
        p1 = s.pair_create(self.e, "甲", "a" * 64, "2026-09-24 10:10:00")
        self.assertEqual(s.pair_active_for_user(self.e, "甲", now)["id"], p1)
        p2 = s.pair_create(self.e, "甲", "b" * 64, "2026-09-24 10:10:00")
        other = s.pair_create(self.e, "乙", "c" * 64, "2026-09-24 10:10:00")
        self.assertEqual(s.pair_get(self.e, p1)["revoked"], 1)     # 新建吊销旧的
        self.assertEqual(s.pair_get(self.e, other)["revoked"], 0)  # 别人的不动
        self.assertEqual(s.pair_by_hash(self.e, "b" * 64)["id"], p2)
        self.assertIsNone(s.pair_by_hash(self.e, ""))
        # 未绑定且过了绑定期限 → 无效
        self.assertIsNone(s.pair_active_for_user(self.e, "甲", "2026-09-24 10:11:00"))
        # 已绑定看会话期限
        s.pair_update(self.e, p2, bound_at=now, session_expires="2026-09-24 22:00:00", dt_name="张某")
        self.assertEqual(s.pair_active_for_user(self.e, "甲", "2026-09-24 21:00:00")["dt_name"], "张某")
        self.assertIsNone(s.pair_active_for_user(self.e, "甲", "2026-09-24 22:00:01"))
        self.assertEqual(s.pair_revoke_user(self.e, "甲"), 1)
        self.assertIsNone(s.pair_active_for_user(self.e, "甲", now))
        with self.assertRaises(Exception):
            s.pair_create(self.e, "丙", "c" * 64, now)  # 令牌哈希唯一
        # 绑定后配对码作废：令牌换成会话令牌的哈希，用过的配对码哈希另存可查
        p3 = s.pair_create(self.e, "丁", "d" * 64, "2026-09-24 10:10:00")
        s.pair_update(self.e, p3, token_hash="e" * 64, qr_hash="d" * 64, bound_at=now)
        self.assertIsNone(s.pair_by_hash(self.e, "d" * 64))
        self.assertEqual(s.pair_by_qr_hash(self.e, "d" * 64)["id"], p3)
        self.assertIsNone(s.pair_by_qr_hash(self.e, ""))


class LaterTests(Base):
    def mk(self, expect, status="open", last=None, **kw):
        cols = dict(inst_id=kw.pop("inst_id", "INST-L"), business_id="B-L", applicant="申请人甲",
                    payee_name="供应商乙", reason="预付货款", expect_date=expect, expect_amount=1000,
                    receiver="会计甲", filed_by="会计乙", status=status, last_remind_at=last)
        cols.update(kw)
        return s.later_insert(self.e, **cols)

    def test_later_open_for_folders_matches_single_lookup(self):
        f1 = s.folder_upsert_from_approval(self.e, {"instId": "IL1", "title": "t1"}, "甲")
        f2 = s.folder_upsert_from_approval(self.e, {"instId": "IL2", "title": "t2"}, "甲")
        f3 = s.folder_create_manual(self.e, "手工", 0, "", "甲")
        a = self.mk("2026-09-30", inst_id="IL1")                        # 按审批实例挂
        self.mk("2026-09-30", inst_id="IL2", status="done")             # 已收齐：不算
        c = self.mk("2026-09-30", inst_id="", folder_id=f3)             # 没实例：按票夹号兜底
        got = s.later_open_for_folders(self.e, [s.folder_get(self.e, x) for x in (f1, f2, f3)])
        self.assertEqual({k: (v or {}).get("id") for k, v in got.items()}, {f1: a, f2: None, f3: c})
        self.assertEqual(s.later_open_for_folders(self.e, []), {})

    def test_crud_and_open_by_inst(self):
        a = self.mk("2026-09-30", inst_id="X")
        la = s.later_get(self.e, a)
        self.assertEqual(la["received_amount"], 0.0)
        self.assertEqual(la["remind_count"], 0)
        self.assertEqual(s.later_open_by_inst(self.e, "X")["id"], a)
        s.later_update(self.e, a, status="done", received_amount="1000")
        self.assertIsNone(s.later_open_by_inst(self.e, "X"))
        self.assertEqual(s.later_get(self.e, a)["received_amount"], 1000.0)
        b = self.mk("2026-10-30", inst_id="X", status="partial")
        self.assertEqual(s.later_open_by_inst(self.e, "X")["id"], b)
        self.assertEqual([r["id"] for r in s.laters_by_inst(self.e, "X")], [b, a])
        self.assertIsNone(s.later_open_by_inst(self.e, ""))

    def test_list_filters_and_paging(self):
        a = self.mk("2026-09-20")                                  # 超期
        b = self.mk("2026-09-30", status="partial", receiver="会计丙", filed_by="会计丙")
        c = self.mk("2026-09-10", status="done")                   # 已收齐，不算超期
        d = self.mk("", payee_name="特殊供应商")                    # 没填预计日
        e_ = self.mk("2026-09-24")                                 # 今天到期，不算超期
        total, rows = s.later_list(self.e, today=TODAY)
        self.assertEqual(total, 5)
        self.assertEqual([r["id"] for r in rows], [a, e_, b, d, c])  # 未收齐在前、按预计日、无日期垫后
        self.assertTrue(rows[0]["overdue"])
        self.assertEqual(rows[0]["days_left"], -4)
        self.assertFalse(rows[1]["overdue"])
        self.assertIsNone(rows[3]["days_left"])
        self.assertFalse(rows[4]["overdue"])
        self.assertEqual([r["id"] for r in s.later_list(self.e, status="overdue", today=TODAY)[1]], [a])
        self.assertEqual(s.later_list(self.e, status="all", today=TODAY)[0], 5)
        self.assertEqual([r["id"] for r in s.later_list(self.e, status="partial", today=TODAY)[1]], [b])
        self.assertEqual([r["id"] for r in s.later_list(self.e, scope_user="会计丙", today=TODAY)[1]], [b])
        self.assertEqual(s.later_list(self.e, scope_user="会计甲", today=TODAY)[0], 4)
        self.assertEqual([r["id"] for r in s.later_list(self.e, q="特殊", today=TODAY)[1]], [d])
        self.assertEqual(s.later_list(self.e, q="预付", today=TODAY)[0], 5)
        self.assertEqual(s.later_list(self.e, q="100%", today=TODAY)[0], 0)  # % 不当通配符
        total, rows = s.later_list(self.e, page=2, size=2, today=TODAY)
        self.assertEqual((total, [r["id"] for r in rows]), (5, [b, d]))

    def test_remind_math(self):
        soon_new = self.mk("2026-09-26")                           # 还有 2 天，没提醒过 → 提醒
        soon_done = self.mk("2026-09-26", last="2026-09-23 10:00:00")  # 窗口起点 09-23 已提醒 → 不提醒
        soon_old = self.mk("2026-09-26", last="2026-09-20 10:00:00")   # 窗口前提醒过 → 提醒
        today_done = self.mk("2026-09-24", last="2026-09-24 10:00:00")  # 今天到期、今天已提醒 → 不提醒
        far = self.mk("2026-10-10")                                # 还早
        over_new = self.mk("2026-09-20")                           # 超期从没提醒 → 提醒
        over_recent = self.mk("2026-09-10", last="2026-09-20 10:00:00")  # 4 天前提醒过 → 不提醒
        over_due = self.mk("2026-09-10", last="2026-09-17 10:00:00")     # 满 7 天 → 提醒
        done = self.mk("2026-09-10", status="done")
        nodate = self.mk("")
        got = {r["id"]: r for r in s.laters_due_for_remind(self.e, TODAY, 3, 7)}
        self.assertEqual(set(got), {soon_new, soon_old, over_new, over_due})
        self.assertEqual(got[soon_new]["remind_reason"], "soon")
        self.assertEqual(got[soon_new]["days_left"], 2)
        self.assertEqual(got[over_due]["remind_reason"], "overdue")
        for x in (soon_done, today_done, far, over_recent, done, nodate):
            self.assertNotIn(x, got)
        # 关掉提前提醒（before_days<0）只剩超期
        got = {r["id"] for r in s.laters_due_for_remind(self.e, TODAY, -1, 7)}
        self.assertEqual(got, {over_new, over_due})
        # every_days=4 → 4 天前那条也该催
        got = {r["id"] for r in s.laters_due_for_remind(self.e, TODAY, 3, 4)}
        self.assertIn(over_recent, got)


class LedgerAuditTests(Base):
    def build(self):
        fa = s.folder_upsert_from_approval(self.e, self.appr("IA", businessId="BA", title="差旅报销",
                                                             applicant="申请人甲"), "甲")
        fb = s.folder_upsert_from_approval(self.e, self.appr("IB", businessId="BB", title="付款给乙",
                                                             applicant="申请人乙"), "甲")
        ids = {}
        ids["a1"] = self.item(fa, "50000000000000000001", review="approved", issue_date="2026-09-01",
                              inv_type="special", verify="green", deductible=1, deduct_status="marked",
                              seller_name="销方丙", seller_tax_id="TAXC", amount=100, tax=13, total=113)
        ids["a2"] = self.item(fa, "50000000000000000002", review="approved", issue_date="2026-09-05",
                              inv_type="normal", deductible=0, seller_name="销方丁", seller_tax_id="TAXD",
                              amount=200, tax=0, total=200)
        ids["b1"] = self.item(fb, "50000000000000000003", review="approved", issue_date="2026-08-15",
                              inv_type="special", verify="red", seller_name="销方丙", seller_tax_id="TAXC",
                              amount=50.5, tax=3.03, total=53.53)
        ids["b2"] = self.item(fb, "50000000000000000004", review="void", issue_date="2026-09-10",
                              amount=999, tax=0, total=999)
        ids["b3"] = self.item(fb, "50000000000000000005", review="approved", status="removed", total=777)
        ids["b4"] = self.item(fb, "50000000000000000006", review="pending", total=1)
        ids["r1"] = self.item(fb, "", kind="receipt", inv_type="", review="approved", issue_date="2026-09-02",
                              amount=10, tax=0, total=10, seller_name="", seller_tax_id="")
        return fa, fb, ids

    def test_ledger(self):
        fa, fb, ids = self.build()
        total, sums, rows = s.ledger_query(self.e, {}, 1, 50)
        self.assertEqual(total, 4)                                 # 默认只看已审核，移除的不出现
        self.assertEqual([r["id"] for r in rows], [ids["a2"], ids["r1"], ids["a1"], ids["b1"]])
        self.assertEqual(sums, {"amount": 360.5, "tax": 16.03, "total": 376.53})
        self.assertEqual(rows[0]["folder"]["business_id"], "BA")
        self.assertEqual(rows[0]["folder"]["payee_name"], "供应商乙")
        self.assertEqual(set(rows[0]["folder"]), {"id", "business_id", "title", "applicant", "template", "dept",
                                                  "payee_name"})
        # review=all：含作废/待审，但合计不含作废
        total, sums, _ = s.ledger_query(self.e, {"review": "all"}, 1, 50)
        self.assertEqual(total, 6)
        self.assertEqual(sums["total"], 377.53)
        total, sums, _ = s.ledger_query(self.e, {"review": "void"}, 1, 50)
        self.assertEqual((total, sums["total"]), (1, 999.0))
        q = lambda **f: [r["id"] for r in s.ledger_query(self.e, f, 1, 50)[2]]
        self.assertEqual(q(q="0003"), [ids["b1"]])
        self.assertEqual(q(q="申请人乙"), [ids["r1"], ids["b1"]])
        self.assertEqual(q(q="差旅"), [ids["a2"], ids["a1"]])
        self.assertEqual(q(q="BB"), [ids["r1"], ids["b1"]])
        self.assertEqual(q(date_from="2026-09-01", date_to="2026-09-02"), [ids["r1"], ids["a1"]])
        self.assertEqual(q(**{"from": "2026-09-03"}), [ids["a2"]])
        self.assertEqual(q(inv_type="special"), [ids["a1"], ids["b1"]])
        self.assertEqual(q(invType="normal"), [ids["a2"]])
        self.assertEqual(q(verify="green"), [ids["a1"]])
        self.assertEqual(q(verify="unset"), [ids["a2"], ids["r1"]])
        self.assertEqual(q(deduct="yes"), [ids["a1"]])
        self.assertEqual(q(deduct="no"), [ids["a2"]])
        self.assertEqual(q(deduct="unset"), [ids["r1"], ids["b1"]])
        self.assertEqual(q(deduct="marked"), [ids["a1"]])
        self.assertEqual(q(seller="TAXC"), [ids["a1"], ids["b1"]])
        self.assertEqual(q(seller="丁"), [ids["a2"]])
        self.assertEqual(q(kind="receipt"), [ids["r1"]])
        # 分页：总数不变，页内条数按 size
        total, _, rows = s.ledger_query(self.e, {}, 2, 3)
        self.assertEqual((total, [r["id"] for r in rows]), (4, [ids["b1"]]))
        total, sums, rows = s.ledger_query(self.e, {"q": "查无此票"}, 1, 50)
        self.assertEqual((total, sums, rows), (0, {"amount": 0.0, "tax": 0.0, "total": 0.0}, []))

    def test_ledger_iter_streams_same_rows_without_json(self):
        """robustness/db P3：导出用的流式取数与分页查询同筛选同顺序，不带 JSON 大列，带 share（拆分按分摊额）。"""
        fa, fb, ids = self.build()
        s.item_update(self.e, ids["a2"], split=1, alloc=50)
        want = [r["id"] for r in s.ledger_query(self.e, {"review": "withvoid"}, 1, 500)[2]]
        got = list(s.ledger_iter(self.e, {"review": "withvoid"}, chunk=2))
        self.assertEqual([r["id"] for r in got], want)
        self.assertFalse({"lines_json", "field_src_json", "flags_json", "pending_json"} & set(got[0]))
        self.assertEqual({r["id"]: r for r in got}[ids["a2"]]["folder"]["business_id"], "BA")
        self.assertEqual({r["id"]: r["share"] for r in got}[ids["a2"]], 50.0)
        self.assertEqual(len(list(s.ledger_iter(self.e, {}, cap=2))), 2)

    def test_ledger_withvoid_only_approved_and_void(self):
        """「含作废」＝已审核＋已作废，不把草稿/待审/退回的票拉进台账；合计仍不含作废。"""
        fa, fb, ids = self.build()
        total, sums, rows = s.ledger_query(self.e, {"review": "withvoid"}, 1, 50)
        self.assertEqual(total, 5)
        self.assertNotIn(ids["b4"], [r["id"] for r in rows])             # 待审的不进
        self.assertIn(ids["b2"], [r["id"] for r in rows])                # 作废的进
        self.assertEqual(sums["total"], 376.53)                          # 合计同"只看已审核"
        self.assertIn("withvoid", s.LEDGER_REVIEWS)

    def test_ledger_and_sellers_sum_split_share_once(self):
        """一张票拆给几张单：台账/销方合计只算各单分摊额，不按票面重复累加。"""
        f1 = s.folder_create_manual(self.e, "拆1", 0, "", "甲")
        f2 = s.folder_create_manual(self.e, "拆2", 0, "", "甲")
        n = "80000000000000000001"
        s.seller_seen(self.e, "TAXS", "拆分销方", "2026-09-01", None)
        for fid, alloc in ((f1, 600), (f2, 400)):
            self.item(fid, n, review="approved", split=1, alloc=alloc, amount=884.96, tax=115.04, total=1000,
                      seller_tax_id="TAXS")
        total, sums, _ = s.ledger_query(self.e, {"q": n}, 1, 50)
        self.assertEqual(total, 2)
        self.assertEqual(sums["total"], 1000.0)
        self.assertAlmostEqual(sums["amount"], 884.96, places=1)
        self.assertAlmostEqual(sums["tax"], 115.04, places=1)
        row = [r for r in s.sellers_query(self.e) if r["tax_id"] == "TAXS"][0]
        self.assertEqual((row["items"], row["total"]), (2, 1000.0))

    def test_audit_queue_tabs(self):
        f_sub = s.folder_create_manual(self.e, "已提交票夹", 0, "", "甲")
        s.folder_update(self.e, f_sub, status="submitted", submitted_at="2026-09-24 09:00:00", payee_name="供应商戊")
        self.item(f_sub, "60000000000000000001", review="pending", pending_json=["seller_name"])
        self.item(f_sub, "60000000000000000002", review="pending", flags_json={"dup": {"kind": "item"}})
        self.item(f_sub, "", kind="other", review="pending")
        self.item(f_sub, "60000000000000000009", review="void", pending_json=["x"])  # 作废不计数
        f_later = s.folder_create_manual(self.e, "后补收票", 0, "", "甲")        # collecting 但有待审票
        s.folder_update(self.e, f_later, updated_at="2026-09-23 09:00:00")
        self.item(f_later, "60000000000000000003", review="pending")
        f_ret = s.folder_create_manual(self.e, "退回票夹", 0, "", "甲")
        s.folder_update(self.e, f_ret, status="returned", review_note="缺纸质件")
        self.item(f_ret, "60000000000000000004", review="returned")
        f_done = s.folder_create_manual(self.e, "已审票夹", 0, "", "甲")
        s.folder_update(self.e, f_done, status="approved", reviewed_at="2026-09-20 10:00:00")
        self.item(f_done, "60000000000000000005", review="approved")
        self.item(f_done, "60000000000000000006", review="void")
        self.item(f_done, "60000000000000000010", review="pending", status="removed")  # 移除的不挡"已审"
        f_done2 = s.folder_create_manual(self.e, "已审票夹2", 0, "", "甲")
        s.folder_update(self.e, f_done2, status="approved", reviewed_at="2026-09-22 10:00:00")
        self.item(f_done2, "60000000000000000011", review="approved")
        f_void = s.folder_create_manual(self.e, "全作废", 0, "", "甲")
        self.item(f_void, "60000000000000000007", review="void")
        f_draft = s.folder_create_manual(self.e, "收票中", 0, "", "甲")
        self.item(f_draft, "60000000000000000008")
        f_empty = s.folder_create_manual(self.e, "空票夹", 0, "", "甲")

        total, rows = s.audit_queue(self.e, "pending", None, 1, 30)
        self.assertEqual(total, 2)
        self.assertEqual([r["id"] for r in rows], [f_later, f_sub])  # 先到先审
        c = rows[1]["counts"]
        self.assertEqual(c, {"pending_items": 3, "invoices": 2, "dup": 1, "unchecked": 1, "others": 1})
        total, rows = s.audit_queue(self.e, "returned", "", 1, 30)
        self.assertEqual((total, rows[0]["id"], rows[0]["review_note"]), (1, f_ret, "缺纸质件"))
        total, rows = s.audit_queue(self.e, "done", "", 1, 30)
        self.assertEqual([r["id"] for r in rows], [f_done2, f_done])   # 最近的在前
        self.assertEqual(rows[1]["counts"]["invoices"], 1)
        for fid in (f_void, f_draft, f_empty):
            self.assertNotIn(fid, [r["id"] for tab in ("pending", "returned", "done")
                                   for r in s.audit_queue(self.e, tab, "", 1, 30)[1]])
        # 搜索：票夹字段 / 票号
        self.assertEqual([r["id"] for r in s.audit_queue(self.e, "pending", "供应商戊", 1, 30)[1]], [f_sub])
        self.assertEqual([r["id"] for r in s.audit_queue(self.e, "pending", "0003", 1, 30)[1]], [f_later])
        self.assertEqual(s.audit_queue(self.e, "pending", "无此单", 1, 30), (0, []))
        total, rows = s.audit_queue(self.e, "pending", "", 2, 1)
        self.assertEqual((total, [r["id"] for r in rows]), (2, [f_sub]))


class BatchTaxlistSellerTests(Base):
    def test_batches_and_taxlist(self):
        b1 = s.batch_insert(self.e, "taxlist", "清单1.xlsx", 3, {"green": 1}, "甲")
        b2 = s.batch_insert(self.e, "opening", "期初.xlsx", 0, None, "甲")
        self.assertEqual(s.batch_get(self.e, b1)["summary_json"], {"green": 1})
        self.assertEqual(s.batch_get(self.e, b2)["summary_json"], {})
        self.assertEqual([b["id"] for b in s.batches_list(self.e)], [b2, b1])
        self.assertEqual([b["id"] for b in s.batches_list(self.e, "taxlist")], [b1])
        s.batch_update(self.e, b1, rows=5)
        self.assertEqual(s.batch_get(self.e, b1)["rows"], 5)
        rows = [dict(number="70000000000000000001", issue_date="2026-09-01", total="113", status="正常",
                     raw={"发票号码": "70000000000000000001"}),
                dict(number="87654321", code="044001900222", total=50, status="已作废"),
                dict(number="70000000000000000001", status="已红冲")]
        self.assertEqual(s.taxlist_insert_many(self.e, b1, rows, "甲"), 3)
        got = s.taxlist_rows(self.e, b1)
        self.assertEqual(got[0]["dup_key"], "N:70000000000000000001")
        self.assertEqual(got[0]["raw_json"]["发票号码"], "70000000000000000001")
        self.assertEqual(got[0]["total"], 113.0)
        self.assertEqual(got[1]["dup_key"], "C:044001900222:87654321")
        self.assertEqual(len(s.taxlist_by_dup(self.e, b1, "N:70000000000000000001")), 2)
        self.assertEqual(s.taxlist_by_dup(self.e, b2, "N:70000000000000000001"), [])
        self.assertEqual(s.taxlist_by_dup(self.e, b1, ""), [])
        self.assertEqual(s.taxlist_insert_many(self.e, b1, [], "甲"), 0)

    def test_sellers(self):
        self.assertTrue(s.seller_seen(self.e, "TAXC", "销方丙", "2026-09-10", 3))
        self.assertFalse(s.seller_seen(self.e, "TAXC", "销方丙", "2026-09-15", 4))  # 更晚：不改
        self.assertEqual(s.seller_get(self.e, "TAXC")["first_seen"], "2026-09-10")
        self.assertFalse(s.seller_seen(self.e, "TAXC", "销方丙", "2026-08-30", 2))  # 更早：改首见
        sc = s.seller_get(self.e, "TAXC")
        self.assertEqual((sc["first_seen"], sc["first_item_id"], sc["check_result"]), ("2026-08-30", 2, "未查"))
        self.assertFalse(s.seller_seen(self.e, "", "无税号", "2026-09-01", 1))
        s.seller_seen(self.e, "TAXD", "销方丁", "2026-09-02", 5)
        for n, tid, total, review in (("1", "TAXC", 100, "approved"), ("2", "TAXC", 50.5, "approved"),
                                      ("3", "TAXC", 999, "void"), ("4", "TAXD", 7, "pending")):
            s.item_insert(self.e, folder_id=1, kind="invoice", number=n, seller_tax_id=tid, total=total, review=review)
        rows = s.sellers_query(self.e)
        self.assertEqual([r["tax_id"] for r in rows], ["TAXD", "TAXC"])
        self.assertEqual((rows[1]["items"], rows[1]["total"]), (2, 150.5))
        self.assertEqual((rows[0]["items"], rows[0]["total"]), (0, 0.0))
        self.assertEqual([r["tax_id"] for r in s.sellers_query(self.e, month="2026-08")], ["TAXC"])
        r = s.seller_check(self.e, "TAXC", "2026-09-24", "信用中国", "无记录", "已查", "会计甲")
        self.assertEqual((r["check_result"], r["checked_by"], r["check_channel"]), ("无记录", "会计甲", "信用中国"))
        self.assertEqual([x["tax_id"] for x in s.sellers_query(self.e, status="unchecked")], ["TAXD"])
        self.assertEqual([x["tax_id"] for x in s.sellers_query(self.e, status="checked")], ["TAXC"])
        self.assertEqual(s.sellers_query(self.e, status="hit"), [])
        self.assertEqual([x["tax_id"] for x in s.sellers_query(self.e, status="无记录")], ["TAXC"])
        new = s.seller_check(self.e, "TAXE", "2026-09-24", "省税务局", "命中", "", "会计甲")  # 没档案先建
        self.assertEqual(new["check_result"], "命中")
        with self.assertRaises(ValueError):
            s.seller_check(self.e, "", "", "", "", "", "")


if __name__ == "__main__":
    unittest.main()
