"""Load a NEW A3 schema; export 8 queries and measured materialization evidence.
Never overwrites a populated schema. --local is practice, --cloud is RDS proof.
"""
import argparse,csv,json,os,re,statistics,time
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
import pymysql
from dotenv import load_dotenv,dotenv_values
R=Path(__file__).resolve().parents[1]
def statements(path):
    text='\n'.join(l for l in path.read_text().splitlines() if not l.lstrip().startswith('--'))
    return [s.strip() for s in text.split(';') if s.strip()]
def queries():
    result={}
    for block in (R/'warehouse/03_queries.sql').read_text().split('-- QUERY ')[1:]:
        name,body=block.split('\n',1)
        sql='\n'.join(l for l in body.splitlines() if not l.startswith('--')).strip().rstrip(';')
        result[name.strip()]=sql
    return result
def connect(local):
    if local:return pymysql.connect(unix_socket=local,user='root',autocommit=False)
    load_dotenv(R/'.env')
    # Visible local-only input file for users whose Finder hides dotfiles.
    if not os.environ.get('RDS_PASSWORD'):
        password=dotenv_values(R/'填写数据库密码.txt').get('RDS_PASSWORD')
        if password:os.environ['RDS_PASSWORD']=password
    if not os.environ.get('RDS_PASSWORD'):raise RuntimeError('Fill RDS_PASSWORD locally in .env or 填写数据库密码.txt before loading RDS')
    host=os.environ.get('RDS_HOST','');ca=os.environ.get('RDS_SSL_CA','')
    if not host.endswith('.rds.amazonaws.com') or not Path(ca).is_file():
        raise RuntimeError('Set the real RDS_HOST and downloaded RDS_SSL_CA in .env')
    if os.environ.get('RDS_DATABASE','rideshare_dw')!='rideshare_dw':raise ValueError('Use dedicated rideshare_dw')
    return pymysql.connect(host=host,port=int(os.environ.get('RDS_PORT',3306)),user=os.environ['RDS_USER'],password=os.environ['RDS_PASSWORD'],
             ssl_ca=ca,ssl_verify_cert=True,ssl_verify_identity=True,connect_timeout=20,read_timeout=180,autocommit=False)
def csvrows(p):
    with p.open(newline='') as f:return list(csv.DictReader(f))
def insert(cur,table,rows):
    if not rows:return
    keys=list(rows[0]);sql=f"INSERT INTO `{table}` ({','.join('`'+k+'`' for k in keys)}) VALUES ({','.join(['%s']*len(keys))})"
    for start in range(0,len(rows),250):
        cur.executemany(sql,[[None if x[k]=='' else x[k] for k in keys] for x in rows[start:start+250]])
def export(cur,sql,out):
    cur.execute(sql);rows=cur.fetchall();cols=[x[0] for x in cur.description]
    with out.with_suffix('.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(cols);w.writerows(rows)
    out.with_suffix('.json').write_text(json.dumps(dict(columns=cols,rows=rows),default=str,indent=2))
    return cols,rows
def profile(cur,sql,out):
    samples=[]
    for i in range(5):
        cur.execute('EXPLAIN ANALYZE '+sql);plan='\n'.join(str(r[0]) for r in cur.fetchall())
        (out.parent/f'{out.name}_{i+1}.txt').write_text(plan)
        times=re.findall(r'actual time=[\d.eE+-]+\.\.([\d.eE+-]+)',plan)
        if not times:raise RuntimeError('No actual execution time in EXPLAIN ANALYZE')
        samples.append(float(times[0]))
    return dict(root_end_ms=samples,median_ms=statistics.median(samples))
def main():
    ap=argparse.ArgumentParser();g=ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--local',metavar='SOCKET');g.add_argument('--cloud',action='store_true')
    ap.add_argument('--action',choices=['load','evidence'],required=True);a=ap.parse_args()
    dest=R/('evidence/local/warehouse' if a.local else 'evidence/cloud/warehouse');dest.mkdir(parents=True,exist_ok=True)
    conn=connect(a.local);cur=conn.cursor()
    if a.action=='load':
        cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='rideshare_dw'")
        if cur.fetchone()[0]:raise RuntimeError('rideshare_dw already contains tables; refusing overwrite. Use evidence action after a completed load.')
        for s in statements(R/'warehouse/01_schema.sql'):cur.execute(s)
        for table,path in [('silver_trip','data/silver/trips_enriched.csv'),('silver_weather','data/silver/weather_hourly.csv'),
          ('gold_hourly_weather','data/gold/hourly_weather.csv'),('gold_community_daily_weather','data/gold/community_daily_weather.csv')]:insert(cur,table,csvrows(R/path))
        refs=[dict(community_code=int(x['area_numbe']),community_name=x['community']) for x in json.loads((R/'data/raw/area_lookup.json').read_text())]
        refs.append(dict(community_code=0,community_name='Unknown'))
        insert(cur,'silver_community_reference',refs)
        for s in statements(R/'warehouse/02_etl.sql'):cur.execute(s)
        # Preserve JSON responses and A2 parsed records in cloud staging; exact original bytes remain archived locally.
        files=sorted((R/'data/bronze').glob('2025-*/page_*.json'))+sorted((R/'data/bronze/weather').glob('202*.json'))
        files=[p for p in files if '.audit.' not in p.name]
        n=0
        for p in files:
            audit=json.loads(p.with_suffix('.audit.json').read_text());n+=1
            when=audit.get('ingested_at',audit.get('retrieved_utc'))
            insert(cur,'bronze_payload',[dict(payload_id=n,source_name='weather' if 'weather' in str(p) else 'TNP candidate pool',
                source_file=str(p.relative_to(R)),ingested_at=datetime.fromisoformat(when).replace(tzinfo=None),
                payload_sha256=audit['sha256'],payload=json.dumps(json.loads(p.read_text())))])
        old=R/'data/bronze/a2_original/chicago_rideshare_wide_raw.csv';meta=json.loads((old.parent/'manifest.json').read_text());oldrows=csvrows(old)
        for i in range(0,len(oldrows),500):
            n+=1;insert(cur,'bronze_payload',[dict(payload_id=n,source_name='A2 original convenience sample',source_file=f'data/bronze/a2_original/rows_{i}_{i+500}',
             ingested_at=datetime.fromisoformat(meta['ingested_at']).replace(tzinfo=None),payload_sha256=meta['sha256'],payload=json.dumps(oldrows[i:i+500]))])
        n=0
        for source in ['trips','weather']:
            for x in json.loads((R/f'data/quarantine/{source}.json').read_text()):
                n+=1;insert(cur,'silver_quarantine',[dict(quarantine_sk=n,source_name=source,reject_reason=x['reason'],payload=json.dumps(x))])
        cur.execute('SELECT COUNT(*) FROM fact_trip');count=cur.fetchone()[0]
        assert count==len(csvrows(R/'data/silver/trips_enriched.csv'))
        conn.commit();print('Loaded new schema; fact trips:',count)
    cur.execute('USE rideshare_dw')
    reconciled={}
    for dimension in ['dim_date','dim_hour','dim_community','dim_weather']:
        expected={int(r[next(iter(r))]):r for r in csvrows(R/f'data/warehouse/{dimension}.csv')}
        cur.execute(f'SELECT * FROM {dimension} ORDER BY 1');actual=cur.fetchall();columns=[c[0] for c in cur.description]
        assert len(actual)==len(expected),dimension+' row count differs'
        for row in actual:
            target=expected[row[0]]
            for column,value in zip(columns,row):
                wanted=target[column]
                if value is None:assert wanted=='',(dimension,column)
                elif isinstance(value,(int,float,Decimal)):assert Decimal(str(value))==Decimal(wanted),(dimension,column)
                else:assert str(value)==wanted,(dimension,column)
        reconciled[dimension]=dict(rows=len(actual),all_values_match=True)
    (dest/'dimension_reconciliation.json').write_text(json.dumps(reconciled,indent=2))
    export(cur,'SELECT VERSION() AS engine_version',dest/'engine_version')
    export(cur,'SELECT * FROM dim_community WHERE community_code=32 ORDER BY valid_from',dest/'scd2_versions')
    invalid="SELECT COUNT(*) AS invalid_links FROM fact_trip f JOIN dim_date d USING(date_sk) JOIN dim_community c ON f.pickup_community_sk=c.community_sk WHERE d.full_date<c.valid_from OR (c.valid_to IS NOT NULL AND d.full_date>=c.valid_to)"
    _,rows=export(cur,invalid,dest/'scd2_invalid_links');assert rows[0][0]==0
    stats={};q=queries()
    for name,sql in q.items():
        export(cur,sql,dest/name);stats[name]=profile(cur,sql,dest/(name+'_baseline'))
        print(name,stats[name]['median_ms'],'ms',flush=True)
    slow=max(stats,key=lambda k:stats[k]['median_ms']);cache='a3_materialized_result'
    cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='rideshare_dw' AND table_name=%s",(cache,))
    exists=cur.fetchone()[0]
    if exists:
        previous=dest/'optimization.json'
        if not previous.exists():raise RuntimeError('Existing cache has no matching evidence manifest; refusing overwrite')
        previous_summary=json.loads(previous.read_text())
        slow=previous_summary['selected_query']
        refresh_ms=previous_summary['materialization_wall_ms']
    else:
        t=time.perf_counter();cur.execute(f'CREATE TABLE {cache} AS '+q[slow]);conn.commit();refresh_ms=(time.perf_counter()-t)*1000
    create=f'CREATE TABLE {cache} AS '+q[slow]
    after_sql=f'SELECT * FROM {cache}';cols,before=export(cur,q[slow],dest/'optimized_original_result')
    cols2,after=export(cur,after_sql,dest/'optimized_cached_result')
    def canon(rows):return sorted(tuple('NULL' if x is None else str(x) for x in r) for r in rows)
    assert cols==cols2 and canon(before)==canon(after),'Materialization changed results'
    afterstats=profile(cur,after_sql,dest/'optimized_after')
    summary=dict(environment='local practice only' if a.local else 'AWS RDS',captured_at=datetime.now(timezone.utc).isoformat(),
      baseline_queries=stats,selected_query=slow,optimization='Materialized query result in a normal MySQL table; explicit refresh required',
      before=stats[slow],after=afterstats,materialization_wall_ms=refresh_ms,cache_created_this_run=not bool(exists),result_sets_equal=True,
      refresh_statement=f'TRUNCATE TABLE {cache}; INSERT INTO {cache} '+q[slow]+';',
      caveat='Cached results become stale when the fact or dimension data changes. Refresh cost and storage are not part of SELECT latency.')
    (dest/'optimization.json').write_text(json.dumps(summary,indent=2))
    (dest/'optimization.sql').write_text('-- Run these two statements on the already loaded schema for before/after evidence.\nUSE rideshare_dw;\nEXPLAIN ANALYZE '+q[slow]+';\nEXPLAIN ANALYZE '+after_sql+';\n\n-- The loader already created the cache with:\n-- '+create.replace('\n','\n-- ')+';\n')
    (dest/'verification.json').write_text(json.dumps(dict(environment=summary['environment'],fact_rows=len(csvrows(R/'data/silver/trips_enriched.csv')),query_count=len(q),scd_invalid_links=0),indent=2))
    print(json.dumps(summary,indent=2));conn.close()

if __name__=='__main__':main()
