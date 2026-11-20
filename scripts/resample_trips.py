"""Monthly two-stage random sampling of public Chicago TNP trips.

Stage 1: SRSWOR of 16 prefix clusters from all 65,536 four-hex prefixes.
Stage 2: SRSWOR of 1,000 records from the COMPLETE selected cluster pool.
Do not call this a full-population simple random sample. For totals the
two-stage expansion weight is (65536 / 16) * (pool_size / sample_size).
No assumptions about uniform/random trip-ID generation are needed for the
random cluster selection; variance estimation must respect cluster sampling.
"""
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from datetime import datetime, timezone
import argparse, csv, hashlib, json, random, time

ROOT = Path(__file__).resolve().parents[1]
API = 'https://data.cityofchicago.org'
SEED = 20261119
CLUSTERS = 16
TARGET = 1000
PAGE = 2000
FIELDS = ['trip_id','trip_start_timestamp','trip_end_timestamp','trip_seconds',
          'trip_miles','pickup_community_area','dropoff_community_area','fare',
          'tip','additional_charges','trip_total','shared_trip_authorized',
          'shared_trip_match','trips_pooled']

def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2))

def fetch(url, path):
    # Resume only previously completed, locally hashed API responses.
    audit = path.with_suffix('.audit.json')
    if path.exists() and audit.exists():
        info = json.loads(audit.read_text())
        payload = path.read_bytes()
        if info['url'] != url or info['sha256'] != hashlib.sha256(payload).hexdigest():
            raise ValueError('Cache provenance mismatch: ' + str(path))
        return json.loads(payload), info
    for attempt in range(5):
        try:
            req = Request(url, headers={'User-Agent':'Chicago rideshare coursework sampling',
                                        'Accept':'application/json'})
            with urlopen(req, timeout=120) as response:
                payload = response.read()
            obj = json.loads(payload)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            info = {'url':url,'retrieved_utc':datetime.now(timezone.utc).isoformat(),
                    'sha256':hashlib.sha256(payload).hexdigest(),'bytes':len(payload),
                    'rows':len(obj) if isinstance(obj,list) else None}
            save(audit, info)
            return obj, info
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            if isinstance(exc, HTTPError) and exc.code not in (429,500,502,503,504):
                raise
            if attempt == 4:
                raise
            delay = min(2 ** (attempt+1), 32)
            if isinstance(exc, HTTPError):
                retry = exc.headers.get('Retry-After', '')
                if retry.isdigit(): delay = min(int(retry), 120)
            print(f'Retry {attempt+1}: {type(exc).__name__}; {delay}s', flush=True)
            time.sleep(delay)
    raise RuntimeError('No response')

def query(params):
    return API + '/resource/6dvr-xwnh.json?' + urlencode(params)

def month_sample(month):
    month_name = f'2025-{month:02}'
    rng = random.Random(f'{SEED}:clusters:{month_name}')
    prefixes = sorted(f'{n:04x}' for n in rng.sample(range(65536), CLUSTERS))
    end = f'2025-{month+1:02}-01' if month < 12 else '2026-01-01'
    where = (f"trip_start_timestamp >= '{month_name}-01T00:00:00' AND "
             f"trip_start_timestamp < '{end}T00:00:00' AND (" +
             ' OR '.join(f"starts_with(trip_id, '{prefix}')" for prefix in prefixes) + ')')
    records, requests = [], []
    for page in range(100):
        url = query({'$select':','.join(FIELDS),'$where':where,
                     '$order':'trip_id','$limit':PAGE,'$offset':page*PAGE})
        rows, info = fetch(url, ROOT/f'data/bronze/{month_name}/page_{page:03}.json')
        if not isinstance(rows,list): raise ValueError('Expected array response')
        for row in rows:
            tid = row.get('trip_id','')
            if len(tid) != 40 or tid[:4] not in prefixes:
                raise ValueError('Unexpected trip ID / selected prefix')
            if not row.get('trip_start_timestamp','').startswith(month_name):
                raise ValueError('Unexpected trip month')
        records.extend(rows); requests.append(info)
        if len(rows) < PAGE: break
        time.sleep(0.4)
    else: raise RuntimeError('Pagination cap reached; refusing partial sampling frame')
    if len({r['trip_id'] for r in records}) != len(records):
        raise ValueError('Duplicates in paginated frame; possible source update')
    counts, count_audit = fetch(query({'$select':'count(*) AS n','$where':where}),
                              ROOT/f'data/bronze/{month_name}/count.json')
    if int(counts[0]['n']) != len(records): raise ValueError('Incomplete sampling frame')
    if len(records) < TARGET:
        raise ValueError('Selected pool below 1000; do not redraw clusters to favor larger pools')
    selected = random.Random(f'{SEED}:rows:{month_name}').sample(records, TARGET)
    selected.sort(key=lambda r: (r['trip_start_timestamp'],r['trip_id']))
    path = ROOT/f'data/raw/tnp_2025_{month:02}.json'
    save(path, selected)
    design = {'month':month_name,'seed':SEED,'selected_prefixes':prefixes,
              'prefix_population':65536,'clusters_selected':CLUSTERS,
              'pool_rows':len(records),'sample_rows':len(selected),
              'expansion_weight':65536/CLUSTERS*len(records)/len(selected),
              'requests':requests,'count_verification':count_audit,
              'sample_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    save(ROOT/f'data/bronze/{month_name}/sampling.json',design)
    print(f'{month_name}: {len(records)} candidates -> {len(selected)} random trips',flush=True)
    return design

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--month',type=int,choices=range(1,13))
    args=ap.parse_args()
    before,_ = fetch(API+'/api/views/6dvr-xwnh.json',ROOT/'data/bronze/metadata_before.json')
    designs=[month_sample(m) for m in ([args.month] if args.month else range(1,13))]
    if args.month: return
    # Recheck source version live: this file must not use a prior cached response.
    after_path=ROOT/'data/bronze/metadata_after.json'
    for p in (after_path,after_path.with_suffix('.audit.json')):
        if p.exists(): p.unlink()
    after,_=fetch(API+'/api/views/6dvr-xwnh.json',after_path)
    version_keys=['rowsUpdatedAt','dataUpdatedAt']
    if any(before.get(k)!=after.get(k) for k in version_keys):
        raise ValueError('Source data version changed: discard cache and restart a new run')
    save(ROOT/'data/sampling_manifest.json',{'method':'monthly two-stage probability sample',
         'seed':SEED,'source_version':{k:before.get(k) for k in version_keys},'months':designs})
    manifest=[]
    for d in designs:
        m=int(d['month'][-2:]); p=ROOT/f'data/raw/tnp_2025_{m:02}.json'
        manifest.append({'name':f'tnp_2025_{m:02}',
          'url':d['requests'][0]['url'],
          'retrieved_utc':d['requests'][0]['retrieved_utc'],
          'sha256':d['sample_sha256'],'rows':TARGET,'bytes':p.stat().st_size,
          'artifact_type':'random sample derived from all paginated API responses',
          'complete_provenance':'data/sampling_manifest.json'})
    url=API+'/resource/igwz-8jzy.json?'+urlencode({'$select':'area_numbe,community',
                                               '$order':'area_numbe','$limit':100})
    areas,info=fetch(url,ROOT/'data/raw/area_lookup.json')
    if len(areas)!=77: raise ValueError('Expected 77 community areas')
    manifest.append(dict(info,name='area_lookup'))
    save(ROOT/'data/raw/retrieval_manifest.json',manifest)
    with (ROOT/'data/sampling_weights.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['trip_id','month','prefix_cluster','expansion_weight'])
        writer.writeheader()
        for d in designs:
            for r in json.loads((ROOT/f"data/raw/tnp_{d['month'].replace('-','_')}.json").read_text()):
                writer.writerow({'trip_id':r['trip_id'],'month':d['month'],
                  'prefix_cluster':r['trip_id'][:4],'expansion_weight':d['expansion_weight']})
    print('Completed random baseline and audit manifest.',flush=True)

if __name__=='__main__': main()
