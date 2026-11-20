"""Independent source, key, hierarchy and lossless canonical-decomposition checks."""
from pathlib import Path
from decimal import Decimal
import csv,json,hashlib,zipfile,xml.etree.ElementTree as E
R=Path(__file__).resolve().parents[1]
def rows(n):return list(csv.DictReader((R/n).open()))
def table(n,key):return {r[key]:r for r in rows('data/normalized/'+n+'.csv')}
raw=rows('data/chicago_rideshare_wide_raw.csv');wide=rows('data/chicago_rideshare_wide.csv');assert len(raw)==len(wide)==12000
assert len(wide[0])==31 and len({r['trip_id'] for r in wide})==12000
for f in json.loads((R/'data/raw/retrieval_manifest.json').read_text()):assert hashlib.sha256((R/'data/raw'/(f['name']+'.json')).read_bytes()).hexdigest()==f['sha256']
trips=table('trips','trip_id');geos=table('geographies','geo_id');extracts=table('source_extracts','extract_id');sources=table('data_sources','source_id')
locations={(r['trip_id'],r['role_code']):r for r in rows('data/normalized/trip_locations.csv')};charges={(r['trip_id'],r['charge_code']):r for r in rows('data/normalized/trip_charges.csv')}
assert len(geos)==80 and {int(g['official_area_code']) for g in geos.values() if g['official_area_code']}==set(range(1,78))
for gid,g in geos.items():
 seen={gid};parent=g['parent_geo_id']
 while parent:
  assert parent not in seen,'Hierarchy cycle';seen.add(parent);parent=geos[parent]['parent_geo_id']
for w in wide:
 t=trips[w['trip_id']];e=extracts[t['extract_id']];s=sources[e['source_id']];result=dict(t)
 for role in ['pickup','dropoff']:
  g=geos[locations[(w['trip_id'],role)]['geo_id']];parent=geos[g['parent_geo_id']]
  result.update({role+'_geo_id':g['geo_id'],role+'_official_area_code':g['official_area_code'],role+'_geo_name':g['geo_name'],role+'_parent_geo_id':parent['geo_id'],role+'_parent_geo_name':parent['geo_name']})
 for code in ['fare','tip','additional_charges']:result[code+'_usd']=charges[(w['trip_id'],code)]['amount_usd']
 result.update(source_id=s['source_id'],source_name=s['source_name'],provider_name=s['provider_name'],source_url=s['source_url'],license_label=s['license_label'],extract_retrieved_at_utc=e['retrieved_at_utc'],extract_request_url=e['request_url'],extract_payload_sha256=e['payload_sha256'])
 assert set(result)==set(w)
 for k in w:
  if k.endswith('_usd') and w[k]:assert Decimal(w[k])==Decimal(result[k]),(k,w[k],result[k])
  else:assert str(w[k])==str(result[k]),(k,w[k],result[k])
assert len(rows('data/1nf_charge_lines.csv'))==36000

from collections import Counter
import random,calendar
manifest=json.loads((R/'data/sampling_manifest.json').read_text())
coverage=[]
for d in manifest['months']:
 month=d['month']; pool=[]
 prefixes=sorted(f'{n:04x}' for n in random.Random(f"20261119:clusters:{month}").sample(range(65536),16))
 assert prefixes==d['selected_prefixes']
 for page in sorted((R/f'data/bronze/{month}').glob('page_*.json')):
  if '.audit.' in page.name:continue
  info=json.loads(page.with_suffix('.audit.json').read_text())
  assert hashlib.sha256(page.read_bytes()).hexdigest()==info['sha256']
  pool.extend(json.loads(page.read_text()))
 assert len(pool)==d['pool_rows'] and len({x['trip_id'] for x in pool})==len(pool)
 sample=json.loads((R/f"data/raw/tnp_{month.replace('-','_')}.json").read_text())
 expected=sorted(random.Random(f"20261119:rows:{month}").sample(pool,1000),key=lambda x:(x['trip_start_timestamp'],x['trip_id']))
 assert expected==sample
 dates={x['trip_start_timestamp'][:10] for x in sample}
 hours={x['trip_start_timestamp'][11:13] for x in sample}
 coverage.append({'month':month,'candidate_pool':len(pool),'sample_rows':len(sample),'distinct_dates':len(dates),'calendar_days':calendar.monthrange(2025,int(month[-2:]))[1],'distinct_hours':len(hours),'min_timestamp':min(x['trip_start_timestamp'] for x in sample),'max_timestamp':max(x['trip_start_timestamp'] for x in sample),'expansion_weight':d['expansion_weight']})
with (R/'evidence/monthly_coverage.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(coverage[0]));w.writeheader();w.writerows(coverage)
for key,label in [('trip_start_timestamp','hour')]:
 counts=Counter(w[key][11:13] for w in wide)
 with (R/'evidence/hourly_coverage.csv').open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['hour','sample_trips']);w.writerows(sorted(counts.items()))
profile=json.loads((R/'data/profile.json').read_text())
report={'status':'pass','sample_rows':len(wide),'unique_trip_ids':len(trips),'distinct_dates':len({w['trip_start_timestamp'][:10] for w in wide}),'distinct_hours':len({w['trip_start_timestamp'][11:13] for w in wide}),'months_verified':len(coverage),'sampling_reproduced_from_raw_api_pages':True,'canonical_rows_reconstructed_from_3nf':len(wide),'normalized_table_counts':profile['normalized_table_counts'],'null_counts':profile['null_counts'],'negative_miles':sum(Decimal(w['trip_miles'])<0 for w in wide if w['trip_miles']),'negative_seconds':sum(int(w['trip_seconds'])<0 for w in wide if w['trip_seconds']),'monthly_coverage':coverage,'cloud_loaded':False}
(R/'evidence/validation.json').write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k not in ['monthly_coverage','null_counts']},indent=2))
