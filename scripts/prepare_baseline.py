"""Create raw wide table, rigorously normalized CSVs, DDL and workbook inputs.
All trips are genuine downloaded records. Internal reference/provenance rows are labeled.
"""
from pathlib import Path
from datetime import datetime
from decimal import Decimal
from collections import Counter,defaultdict
import json,csv,re,hashlib
ROOT=Path(__file__).resolve().parents[1]
def dump(name,obj): (ROOT/name).write_text(json.dumps(obj,indent=2,default=str))
def writecsv(name,rows,fields=None):
 if fields is None:fields=list(rows[0])
 with (ROOT/name).open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def col(name,typ,null=False,pk=False,fk=None):return dict(name=name,type=typ,nullable=null,pk=pk,fk=fk)
SCHEMA={
'data_sources':[col('source_id','VARCHAR(16)',pk=True),col('source_name','VARCHAR(120)'),col('provider_name','VARCHAR(80)'),col('source_url','VARCHAR(255)'),col('license_label','VARCHAR(80)'),col('terms_url','VARCHAR(255)')],
'source_extracts':[col('extract_id','SMALLINT UNSIGNED',pk=True),col('source_id','VARCHAR(16)',fk='data_sources.source_id'),col('retrieved_at_utc','DATETIME(6)'),col('coverage_start','DATE',True),col('coverage_end_exclusive','DATE',True),col('request_url','TEXT'),col('payload_sha256','CHAR(64)')],
'geographies':[col('geo_id','SMALLINT UNSIGNED',pk=True),col('geo_type','VARCHAR(16)'),col('official_area_code','TINYINT UNSIGNED',True),col('geo_name','VARCHAR(80)'),col('parent_geo_id','SMALLINT UNSIGNED',True,fk='geographies.geo_id'),col('reference_extract_id','SMALLINT UNSIGNED',True,fk='source_extracts.extract_id')],
'location_roles':[col('role_code','VARCHAR(8)',pk=True),col('role_name','VARCHAR(30)')],
'trips':[col('trip_id','CHAR(40)',pk=True),col('extract_id','SMALLINT UNSIGNED',fk='source_extracts.extract_id'),col('trip_start_timestamp','DATETIME'),col('trip_end_timestamp','DATETIME',True),col('trip_seconds','INT UNSIGNED',True),col('trip_miles','DECIMAL(10,4)',True),col('shared_trip_authorized','BOOLEAN',True),col('shared_trip_match','BOOLEAN',True),col('trips_pooled','SMALLINT UNSIGNED',True),col('reported_total_usd','DECIMAL(10,2)',True)],
'trip_locations':[col('trip_id','CHAR(40)',pk=True,fk='trips.trip_id'),col('role_code','VARCHAR(8)',pk=True,fk='location_roles.role_code'),col('geo_id','SMALLINT UNSIGNED',fk='geographies.geo_id')],
'charge_types':[col('charge_code','VARCHAR(20)',pk=True),col('charge_name','VARCHAR(40)')],
'trip_charges':[col('trip_id','CHAR(40)',pk=True,fk='trips.trip_id'),col('charge_code','VARCHAR(20)',pk=True,fk='charge_types.charge_code'),col('amount_usd','DECIMAL(10,2)',True)]}
CHECKS={
'geographies':["CHECK (geo_type IN ('REPORTING_ROOT','CITY','COMMUNITY','UNKNOWN'))","CHECK (parent_geo_id IS NULL OR parent_geo_id <> geo_id)","CHECK ((geo_type = 'COMMUNITY' AND official_area_code BETWEEN 1 AND 77 AND parent_geo_id = 2) OR (geo_type <> 'COMMUNITY' AND official_area_code IS NULL))","UNIQUE KEY uq_official_area (official_area_code)"],
'location_roles':["CHECK (role_code IN ('pickup','dropoff'))"],
'trips':["CHECK (trip_miles IS NULL OR trip_miles >= 0)","CHECK (trips_pooled IS NULL OR trips_pooled >= 1)","CHECK (shared_trip_authorized IS NULL OR shared_trip_authorized IN (0,1))","CHECK (shared_trip_match IS NULL OR shared_trip_match IN (0,1))"],
'charge_types':["CHECK (charge_code IN ('fare','tip','additional_charges'))"]}
def ddl():
 chunks=["-- Assignment 3 / Chicago Ride-share / Resampled baseline\n-- MySQL 8.0.18+ syntax; RDS MySQL 8.4 recommended. No credentials.\n-- Uses the existing user-created RDS schema. No CREATE DATABASE or DROP statements.\nUSE rideshare_a3_baseline;\nSET SESSION sql_mode = 'STRICT_TRANS_TABLES,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';"]
 for table,columns in SCHEMA.items():
  lines=[]
  for c in columns:
   typ=c['type']+(' CHARACTER SET ascii COLLATE ascii_bin' if c['name'] in ['trip_id','payload_sha256'] else '')
   lines.append(f"  {c['name']} {typ} {'NULL' if c['nullable'] else 'NOT NULL'}")
  lines.append('  PRIMARY KEY ('+', '.join(c['name'] for c in columns if c['pk'])+')')
  for c in columns:
   if c['fk']:
    dest,key=c['fk'].split('.');lines.append(f"  CONSTRAINT fk_{table}_{c['name']} FOREIGN KEY ({c['name']}) REFERENCES {dest} ({key}) ON UPDATE RESTRICT ON DELETE RESTRICT")
  lines += ['  '+s for s in CHECKS.get(table,[])]
  chunks.append(f"CREATE TABLE {table} (\n"+',\n'.join(lines)+'\n) ENGINE=InnoDB;')
 return '\n\n'.join(chunks)+'\n'
def sql_literal(x):
 if x is None or x=='':return 'NULL'
 if isinstance(x,bool):return '1' if x else '0'
 if isinstance(x,(int,float,Decimal)):return str(x)
 return "'"+str(x).replace('\\','\\\\').replace("'","''")+"'"
def main():
 raw=ROOT/'data/raw';manifest=json.loads((raw/'retrieval_manifest.json').read_text());byname={x['name']:x for x in manifest}
 areas=json.loads((raw/'area_lookup.json').read_text());lookup={int(a['area_numbe']):a['community'] for a in areas};assert set(lookup)==set(range(1,78))
 terms='https://www.chicago.gov/city/en/narr/foia/data_disclaimer.html'
 sources=[dict(source_id=i,source_name=n,provider_name='City of Chicago',source_url='https://data.cityofchicago.org/d/'+i,license_label='SEE_TERMS_OF_USE',terms_url=terms) for i,n in [('6dvr-xwnh','Transportation Network Providers - Trips (2025-)'),('igwz-8jzy','Community Areas')]]
 extracts=[]
 for idx,name in [(1,'area_lookup')]+[(m+1,f'tnp_2025_{m:02}') for m in range(1,13)]:
  m=idx-1;info=byname[name]
  extracts.append(dict(extract_id=idx,source_id='igwz-8jzy' if idx==1 else '6dvr-xwnh',retrieved_at_utc=datetime.fromisoformat(info['retrieved_utc']).strftime('%Y-%m-%d %H:%M:%S.%f'),coverage_start=None if idx==1 else f'2025-{m:02}-01',coverage_end_exclusive=None if idx==1 else (f'2025-{m+1:02}-01' if m<12 else '2026-01-01'),request_url=info['url'],payload_sha256=info['sha256']))
 geos=[dict(geo_id=1,geo_type='REPORTING_ROOT',official_area_code=None,geo_name='Reporting geography',parent_geo_id=None,reference_extract_id=None),dict(geo_id=2,geo_type='CITY',official_area_code=None,geo_name='Chicago',parent_geo_id=1,reference_extract_id=None),dict(geo_id=0,geo_type='UNKNOWN',official_area_code=None,geo_name='Unknown / Unassigned',parent_geo_id=1,reference_extract_id=None)]
 geos += [dict(geo_id=100+a,geo_type='COMMUNITY',official_area_code=a,geo_name=n,parent_geo_id=2,reference_extract_id=1) for a,n in sorted(lookup.items())]
 gmap={g['geo_id']:g for g in geos};trips=[];locations=[];charges=[];wide=[];original=[]
 for month in range(1,13):
  for rownum,r in enumerate(json.loads((raw/f'tnp_2025_{month:02}.json').read_text()),1):
   assert re.fullmatch('[0-9a-f]{40}',r['trip_id'])
   tr=dict(trip_id=r['trip_id'],extract_id=month+1,trip_start_timestamp=r['trip_start_timestamp'][:19].replace('T',' '),trip_end_timestamp=r.get('trip_end_timestamp','')[:19].replace('T',' ') or None,trip_seconds=int(r['trip_seconds']) if r.get('trip_seconds') is not None else None,trip_miles=Decimal(r['trip_miles']) if r.get('trip_miles') is not None else None,shared_trip_authorized=r.get('shared_trip_authorized'),shared_trip_match=r.get('shared_trip_match'),trips_pooled=int(r['trips_pooled']) if r.get('trips_pooled') is not None else None,reported_total_usd=Decimal(r['trip_total']).quantize(Decimal('0.01')) if r.get('trip_total') is not None else None)
   trips.append(tr);w=dict(tr);w['reported_total_usd']=Decimal(r['trip_total']) if r.get('trip_total') is not None else None
   for role in ['pickup','dropoff']:
    source_code=r.get(role+'_community_area');code=int(source_code) if source_code is not None else None;gid=100+code if code in lookup else 0
    locations.append(dict(trip_id=r['trip_id'],role_code=role,geo_id=gid));g=gmap[gid]
    w.update({role+'_geo_id':gid,role+'_official_area_code':code,role+'_geo_name':g['geo_name'],role+'_parent_geo_id':g['parent_geo_id'],role+'_parent_geo_name':gmap[g['parent_geo_id']]['geo_name']})
   for code in ['fare','tip','additional_charges']:
    val=Decimal(r[code]) if r.get(code) is not None else None
    charges.append(dict(trip_id=r['trip_id'],charge_code=code,amount_usd=val));w[code+'_usd']=val
   w.update(source_id='6dvr-xwnh',source_name=sources[0]['source_name'],provider_name='City of Chicago',source_url=sources[0]['source_url'],license_label='SEE_TERMS_OF_USE',extract_retrieved_at_utc=extracts[month]['retrieved_at_utc'],extract_request_url=extracts[month]['request_url'],extract_payload_sha256=extracts[month]['payload_sha256'])
   wide.append(w);original.append(r)
 roles=[dict(role_code='pickup',role_name='Pickup'),dict(role_code='dropoff',role_name='Dropoff')]
 types=[dict(charge_code=k,charge_name=v) for k,v in [('fare','Fare'),('tip','Tip'),('additional_charges','Additional charges')]]
 tables=dict(data_sources=sources,source_extracts=extracts,geographies=geos,location_roles=roles,trips=trips,trip_locations=locations,charge_types=types,trip_charges=charges)
 assert len(trips)==12000 and len({t['trip_id'] for t in trips})==12000
 writecsv('data/chicago_rideshare_wide_raw.csv',wide)
 for w in wide:
  if w['reported_total_usd'] is not None:w['reported_total_usd']=w['reported_total_usd'].quantize(Decimal('0.01'))
 writecsv('data/chicago_rideshare_wide.csv',wide)
 for name,rows in tables.items():writecsv('data/normalized/'+name+'.csv',rows,[x['name'] for x in SCHEMA[name]])
 (ROOT/'sql/01_schema.sql').write_text(ddl())
 seed=['-- Genuine data transformed from raw public JSON; internal reference rows are documented.','USE rideshare_a3_baseline;','START TRANSACTION;']
 for name,rows in tables.items():
  keys=[x['name'] for x in SCHEMA[name]]
  for start in range(0,len(rows),250):
   group=rows[start:start+250];seed.append(f"INSERT INTO {name} ("+', '.join(keys)+') VALUES\n'+',\n'.join('('+', '.join(sql_literal(row[k]) for k in keys)+')' for row in group)+';')
 seed+=['COMMIT;'];(ROOT/'sql/02_load_data.sql').write_text('\n'.join(seed)+'\n')
 missing={k:sum(w[k] is None for w in wide) for k in wide[0]};duplicates={k:len(wide)-len({str(w[k]) for w in wide}) for k in ['trip_id','pickup_geo_name','dropoff_geo_name','source_id']}
 mismatch=[r for r in original if all(r.get(k) is not None for k in ['fare','tip','additional_charges','trip_total']) and sum(Decimal(r[k]) for k in ['fare','tip','additional_charges'])!=Decimal(r['trip_total'])]
 popular=Counter(w['pickup_geo_id'] for w in wide if w['pickup_geo_id']!=0).most_common(1)[0];repeated=[w for w in wide if w['pickup_geo_id']==popular[0]]
 drop_counts=Counter(x['dropoff_geo_id'] for x in wide)
 singleton=next((w for w in wide if drop_counts[w['dropoff_geo_id']]==1),None)
 all_role_counts=Counter(w[k] for w in wide for k in ['pickup_geo_id','dropoff_geo_id'] if w[k]!=0)
 rare_id,rare_occurrences=all_role_counts.most_common()[-1]
 rare_rows=[w for w in wide if w['pickup_geo_id']==rare_id or w['dropoff_geo_id']==rare_id]
 profile={'option':'B: public TNP data plus official area lookup, with deterministic enrichment','trip_rows':len(trips),'wide_columns':len(wide[0]),'wide_column_names':list(wide[0]),'normalized_table_counts':{k:len(v) for k,v in tables.items()},'normalized_total_rows':sum(len(v) for v in tables.values()),'sample_design':'Monthly two-stage probability sample: 16 uniformly selected four-hex-digit trip-ID prefix clusters out of 65,536; simple random sample of 1,000 trips from the complete selected-cluster pool. Fixed seed 20261119. See sampling_manifest.json and sampling_weights.csv. Not a full-population simple random sample.','null_counts':missing,'duplicate_group_counts':duplicates,'unknown_pickups':sum(w['pickup_geo_id']==0 for w in wide),'unknown_dropoffs':sum(w['dropoff_geo_id']==0 for w in wide),'reported_component_mismatches':sum(abs(sum(Decimal(r[k]) for k in ['fare','tip','additional_charges'])-Decimal(r['trip_total']))>Decimal('0.005') for r in mismatch),'raw_numeric_serialization_differences':len(mismatch),'financial_complete_rows':sum(r.get('trip_total') is not None for r in original),'five_row_preview':wide[:5],'anomaly_examples':{'update_rows':[{k:w[k] for k in ['trip_id','pickup_geo_id','pickup_geo_name']} for w in repeated[:2]],'update_repeated_occurrences':popular[1],'deletion_row':{k:(singleton or wide[0])[k] for k in ['trip_id','dropoff_geo_id','dropoff_geo_name']},'singleton_dropoff':bool(singleton),'deletion_geo_id':rare_id,'deletion_geo_name':gmap[rare_id]['geo_name'],'deletion_trip_count':len(rare_rows),'deletion_trip_ids':[w['trip_id'] for w in rare_rows],'insertion_example_area':next(g for g in geos if g['geo_type']=='COMMUNITY')},'source_coverage':{f'2025-{m:02}':1000 for m in range(1,13)},'note':'Extraction payload hashes identify selected sample artifacts; original API responses and complete request provenance are preserved in data/bronze and sampling_manifest.json. Null monetary values remain NULL, not zero. Original raw JSON and raw wide CSV are preserved. Canonical reported totals use cents, normalizing source numeric-serialization differences; all complete records reconcile at cent precision. Keep reported total as an observed source measure rather than silently deriving it.'}
 dump('data/profile.json',profile);dump('data/schema_model.json',SCHEMA)
 dictionary=[]
 for name in wide[0]:
  v=[w[name] for w in wide if w[name] is not None];dictionary.append({'column':name,'observed_python_type':type(v[0]).__name__ if v else 'all NULL','non_null_count':len(v),'null_count':len(wide)-len(v),'origin':('Internal extraction provenance identifier' if name=='extract_id' else 'Public TNP community code; NULL preserved' if name in ['pickup_official_area_code','dropoff_official_area_code'] else 'Public TNP field / renamed measure' if name in tr or name in ['fare_usd','tip_usd','additional_charges_usd'] else 'Official lookup enrichment or internal provenance mapping')})
 writecsv('data/data_dictionary.csv',dictionary)
 # Full 1NF relation: a charge line with composite key; no invented claims of non-atomic raw cells.
 line_rows=[];type_names={t['charge_code']:t['charge_name'] for t in types}
 for w in wide:
  for code in type_names:
   line={k:v for k,v in w.items() if k not in ['fare_usd','tip_usd','additional_charges_usd']};line.update(charge_code=code,charge_name=type_names[code],amount_usd=w[code+'_usd']);line_rows.append(line)
 writecsv('data/1nf_charge_lines.csv',line_rows)
 # Workbook shows relations and actual samples, not 36,000 repetitions on screen.
 fds=[('trip_id','extract_id, timestamps, seconds, miles, pooling flags, reported_total, pickup_geo_id, dropoff_geo_id','Partial dependency in 1NF: only part of (trip_id, charge_code)'),('charge_code','charge_name','Partial dependency in 1NF'),('trip_id + charge_code','amount_usd','Full composite-key dependency'),('extract_id','source_id, retrieved_at_utc, coverage dates, request_url, payload_sha256','Transitive through trips in 2NF'),('source_id','source_name, provider_name, source_url, license_label, terms_url','Transitive through extract_id'),('geo_id','geo_type, official_area_code, geo_name, parent_geo_id, reference_extract_id','Transitive through pickup/dropoff IDs'),('parent_geo_id','parent geo_name','Self-reference; remove repeated parent names'),('role_code','role_name','Role reference relation'),('trip_id + role_code','geo_id','Trip-location assignment')]
 dump('data/workbook_input.json',{'schema':SCHEMA,'tables':tables,'wide_preview':wide[:5],'line_preview':line_rows[:9],'fds':fds,'profile':profile})
 print(json.dumps({k:profile[k] for k in ['trip_rows','wide_columns','normalized_table_counts','normalized_total_rows','unknown_pickups','unknown_dropoffs','reported_component_mismatches']},indent=2))
if __name__=='__main__':main()
