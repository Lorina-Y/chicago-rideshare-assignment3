"""Live weather API ingestion with raw preservation and complete-window gates.
Monthly time windows are batching, not a native API cursor. The accompanying
resample_trips.py supplies genuine offset pagination of the domain source.
"""
import calendar, hashlib, json, time
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/bronze/weather'
FIELDS=['temperature_2m','precipitation','snowfall','wind_speed_10m','weather_code']

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    session=requests.Session(); session.headers['User-Agent']='Chicago coursework weather enrichment'
    manifest=[]
    windows=[(f'2025-{m:02}-01',f'2025-{m:02}-{calendar.monthrange(2025,m)[1]}') for m in range(1,13)]
    # UTC padding covers the final local hours and the end-labelled precipitation interval.
    windows.append(('2026-01-01','2026-01-02'))
    for start,end in windows:
        p=OUT/f'{start}.json'; audit=p.with_suffix('.audit.json')
        params=dict(latitude=41.8781,longitude=-87.6298,start_date=start,end_date=end,
                    hourly=','.join(FIELDS),timezone='UTC',timeformat='unixtime',models='era5')
        if p.exists() and audit.exists():
            info=json.loads(audit.read_text()); raw=p.read_bytes()
            assert info['params']==params and info['sha256']==hashlib.sha256(raw).hexdigest()
            data=json.loads(raw)
        else:
            for attempt in range(6):
                try:
                    r=session.get('https://archive-api.open-meteo.com/v1/archive',params=params,timeout=(10,90))
                    if r.status_code in (429,500,502,503,504):
                        delay=r.headers.get('Retry-After','')
                        time.sleep(min(int(delay),60) if delay.isdigit() else min(2**attempt,32))
                        continue
                    r.raise_for_status(); data=r.json();raw=r.content
                    break
                except (requests.Timeout,requests.ConnectionError,requests.exceptions.JSONDecodeError):
                    if attempt==5: raise
                    time.sleep(min(2**attempt,32))
            else: raise RuntimeError(f'Weather window failed: {start}')
            info=dict(params=params,url=r.url,ingested_at=datetime.now(timezone.utc).isoformat(),
                      source_name='Open-Meteo ERA5 historical weather',sha256=hashlib.sha256(raw).hexdigest())
            p.write_bytes(raw);audit.write_text(json.dumps(info,indent=2));time.sleep(0.5)
        hourly=data.get('hourly',{}); timestamps=hourly.get('time',[])
        expected=(datetime.fromisoformat(end)-datetime.fromisoformat(start)).days*24+24
        if len(timestamps)!=expected or any(len(hourly.get(k,[]))!=expected for k in FIELDS):
            raise RuntimeError(f'Incomplete hourly arrays: {start}')
        if len(set(timestamps))!=len(timestamps) or any(b-a!=3600 for a,b in zip(timestamps,timestamps[1:])):
            raise RuntimeError('Non-contiguous UTC weather hours')
        manifest.append(dict(info,file=p.name,rows=len(timestamps)))
        print(f'{start}: {len(timestamps)} hourly records verified',flush=True)
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print('Complete weather ingestion:',sum(x['rows'] for x in manifest),flush=True)

if __name__=='__main__':main()
