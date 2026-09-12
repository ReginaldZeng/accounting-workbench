import unittest
from kernels import ec_preparation as p, ec_documents as docs


class PreparationTests(unittest.TestCase):
    def test_empty_and_partial_are_never_ready(self):
        self.assertEqual(p.progress([])['readiness'],'linked')
        self.assertFalse(p.progress([])['configured'])
        rows=[{'available':True,'state':'ready','file_count':7},{'available':False,'state':'missing'}]
        self.assertEqual(p.progress(rows),dict(ready=1,required=2,files=7,readiness='linked',configured=True))
        rows[1].update(available=True,state='warning')
        self.assertEqual(p.progress(rows)['ready'],1)
        rows[1]['state']='ready'
        self.assertEqual(p.progress(rows)['readiness'],'ready')

    def test_shop_configuration(self):
        self.assertEqual(len(p.requirements({},p.TARGET)),7)
        self.assertEqual(p.requirements({},'another shop'),[])
        self.assertEqual(p.requirements({p.TARGET:['alipay','order']},p.TARGET),['order','alipay'])
        self.assertEqual(p.requirements({p.TARGET:[]},p.TARGET),[])
        for bad in ([],{'s':['unknown']},{'s':['order','order']}):
            with self.assertRaises(ValueError):p.validate(bad)

    def test_snapshot_bridge_keeps_ids_without_guessing(self):
        b=docs.snapshot_batch('shop','2026-07','order',[{'order_no':'1234567890123456789','confirmed_payout':14}], 'snapshot:1',['random.xlsx'])
        row=b['docs'][0]
        self.assertEqual(row['order_no'],'1234567890123456789')
        self.assertEqual(row['kind'],'order_snapshot')
        self.assertEqual(row['period'],'2026-07')
        self.assertIn('非原文件行号',row['sheet'])
        self.assertIsNone(docs.snapshot_batch('shop','2026-07','item',[{'order_key':'hash-only'}],'snapshot:2',[]))
