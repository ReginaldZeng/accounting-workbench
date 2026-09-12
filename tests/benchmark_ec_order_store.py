"""Optional local benchmark: normalized whitelist fixture, disposable DB, aggregate output only."""
import argparse,gzip,json,tempfile,time,statistics
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine
from kernels import ec_order_store as store,ec_workbench as model

def main():
    parser=argparse.ArgumentParser();parser.add_argument('fixture');args=parser.parse_args()
    raw=json.loads(gzip.decompress(Path(args.fixture).read_bytes()))
    sources={k:v['payload'] for k,v in raw['sources'].items()}
    for source in sources.values():source.setdefault('period',raw['period'])
    start=time.perf_counter();rows=model.build_orders(sources,None);compute=time.perf_counter()-start
    dataset=dict(rows=rows,sources=sources,provenance={},kingdee={'available':False},rule={'recognition':'shipment'},cash_available=True)
    with tempfile.TemporaryDirectory(prefix='order-benchmark-') as folder:
        path=Path(folder)/'results.sqlite';engine=create_engine('sqlite:///'+path.as_posix());store.md.create_all(engine)
        store.observe(engine,raw['period'],raw['shop'],'benchmark')
        start=time.perf_counter();store.publish(engine,store.claim(engine,raw['period'],raw['shop']),dataset);persist=time.perf_counter()-start
        expected=model.metrics(rows,True)
        assert store.summary(engine,raw['period'],raw['shop'])[0]==expected
        timings={}
        with patch.object(model,'build_orders',side_effect=AssertionError('rebuild during read')),patch.object(model,'metrics',side_effect=AssertionError('aggregate during read')):
            for name,params in [('page1',{}),('page2',{'page':2}),('last_page',{'page':99999}),('order_search',{'q':rows[0]['order_no']})]:
                samples=[]
                for _ in range(7):
                    start=time.perf_counter();result=store.page(engine,raw['period'],raw['shop'],**params);samples.append((time.perf_counter()-start)*1000)
                timings[name+'_median_ms']=round(statistics.median(samples),2)
                assert result['total'] is not None
            engine.dispose();engine=create_engine('sqlite:///'+path.as_posix())
            assert store.page(engine,raw['period'],raw['shop'])['total']==len(rows)
        size=path.stat().st_size;engine.dispose()
    print(json.dumps(dict(environment='local SQLite; not server timings',orders=len(rows),source_rows={k:len(v.get('rows',[])) for k,v in sources.items()},compute_seconds=round(compute,3),persist_seconds=round(persist,3),database_mb=round(size/1048576,2),metrics_equal=True,restart_persisted=True,**timings),ensure_ascii=False))

if __name__=='__main__':main()
