"""Bronze -> quality checks -> pseudonymized Silver -> Gold / star inputs.
Weather precipitation at t+1h describes the interval [t,t+1h); temperature
and wind are at t. Ambiguous TNP local times retain their trip with weather=0.
"""
import argparse, calendar, csv, hashlib, hmac, json, secrets, shutil
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

R=Path(__file__).resolve().parents[1]; Z=ZoneInfo('America/Chicago')
def readcsv(p):
    with p.open(newline='') as f:return list(csv.DictReader(f))
def dump(p,obj):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(obj,indent=2,default=str))
def writecsv(p,rows,fields=None):
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields or list(rows[0]));w.writeheader();w.writerows(rows)
def resolve_local(t):
    candidates={}
    for fold in (0,1):
        u=t.replace(tzinfo=Z,fold=fold).astimezone(timezone.utc)
        if u.astimezone(Z).replace(tzinfo=None)==t:candidates[int(u.timestamp())]=u
    if len(candidates)==1:return next(iter(candidates.values())),'matched'
    return None,'ambiguous_local_time' if candidates else 'nonexistent_local_time'
def num(v):return None if v is None or v=='' else Decimal(str(v))
def money(v):
    d=num(v);return None if d is None else d.quantize(Decimal('.01'))
def summarize(rows):
    vals=[r for r in rows if r['total_usd'] is not None]
    weight=sum((r['sample_weight'] for r in rows),Decimal(0))
    denom=sum((r['sample_weight'] for r in vals),Decimal(0))
    return dict(sample_trips=len(rows),estimated_trips=weight,
        valid_amount_trips=len(vals),sample_total_usd=sum((r['total_usd'] for r in vals),Decimal(0)),
        weighted_mean_total_usd=(sum((r['total_usd']*r['sample_weight'] for r in vals),Decimal(0))/denom).quantize(Decimal('.0001')) if denom else None,
        sample_mean_miles=(sum((r['trip_miles'] for r in rows if r['trip_miles'] is not None),Decimal(0))/sum(r['trip_miles'] is not None for r in rows)).quantize(Decimal('.0001')) if any(r['trip_miles'] is not None for r in rows) else None)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--a2-csv',type=Path);a=ap.parse_args()
    for d in ['data/silver','data/gold','data/quarantine','data/warehouse','evidence/local','.private']:(R/d).mkdir(parents=True,exist_ok=True)
    old=R/'data/bronze/a2_original/chicago_rideshare_wide_raw.csv'
    if a.a2_csv:
        source=a.a2_csv.read_bytes();old.parent.mkdir(parents=True,exist_ok=True)
        if old.exists() and old.read_bytes()!=source:raise ValueError('A2 archival copy differs')
        old.write_bytes(source)
        dump(old.parent/'manifest.json',dict(source_name='A2 frozen convenience sample',
             ingested_at=datetime.now(timezone.utc).isoformat(),sha256=hashlib.sha256(source).hexdigest(),rows=len(readcsv(old))))
    if not old.exists():raise RuntimeError('Provide --a2-csv to archive the unchanged original A2 sample')
    assert hashlib.sha256(old.read_bytes()).hexdigest()==json.loads((old.parent/'manifest.json').read_text())['sha256']
    keypath=R/'.private/token.key'
    if not keypath.exists():keypath.write_bytes(secrets.token_bytes(32));keypath.chmod(0o600)
    key=keypath.read_bytes()
    qc=[];bad_weather=[];weather_raw={};wm=json.loads((R/'data/bronze/weather/manifest.json').read_text())
    for m in wm:
        p=R/'data/bronze/weather'/m['file'];assert hashlib.sha256(p.read_bytes()).hexdigest()==m['sha256']
        h=json.loads(p.read_text())['hourly']
        for i,t in enumerate(h['time']):
            if t in weather_raw:raise ValueError('Overlapping weather UTC hours')
            weather_raw[t]={k:v[i] for k,v in h.items()}
    checks=Counter();fails=Counter();valid={}
    for ts,x in sorted(weather_raw.items()):
        rules={'weather_required':all(x.get(k) is not None for k in ['time','temperature_2m','precipitation','snowfall','wind_speed_10m','weather_code']),
          'weather_temperature_range':x.get('temperature_2m') is not None and -60<=x['temperature_2m']<=60,
          'weather_nonnegative':all(x.get(k) is not None and x[k]>=0 for k in ['precipitation','snowfall','wind_speed_10m'])}
        for k,ok in rules.items():checks[k]+=1;fails[k]+=not ok
        if all(rules.values()):valid[ts]=x
        else:bad_weather.append(dict(utc_epoch=ts,reason=';'.join(k for k,v in rules.items() if not v),payload=x))
    weathers=[dict(weather_sk=0,weather_hour_utc=None,local_hour=None,utc_offset_minutes=None,temperature_c=None,precipitation_mm=None,snowfall_cm=None,wind_kmh=None,weather_category='Unknown')]
    weather_lookup={}
    for ts,x in sorted(valid.items()):
        utc=datetime.fromtimestamp(ts,timezone.utc);local=utc.astimezone(Z)
        if local.year!=2025:continue
        nxt=valid.get(ts+3600)
        if nxt is None:raise ValueError('Missing end-of-interval precipitation')
        rain=nxt['precipitation'];snow=nxt['snowfall'];wind=x['wind_speed_10m']
        cat='Snow' if snow>0 else 'Rain' if rain>0 else 'Windy' if wind>=35 else 'Dry'
        row=dict(weather_sk=len(weathers),weather_hour_utc=utc.strftime('%Y-%m-%d %H:%M:%S'),
            local_hour=local.strftime('%Y-%m-%d %H:%M:%S'),utc_offset_minutes=int(local.utcoffset().total_seconds()/60),
            temperature_c=x['temperature_2m'],precipitation_mm=rain,snowfall_cm=snow,wind_kmh=wind,weather_category=cat)
        weathers.append(row);weather_lookup[ts]=row
    if len(weathers)-1!=8760:raise ValueError('Incomplete 2025 local-year weather coverage')
    areas={int(x['area_numbe']):x['community'] for x in json.loads((R/'data/raw/area_lookup.json').read_text())}
    weights={x['trip_id']:Decimal(x['expansion_weight']) for x in readcsv(R/'data/sampling_weights.csv')}
    sample=[]
    for m in range(1,13):sample.extend(json.loads((R/f'data/raw/tnp_2025_{m:02}.json').read_text()))
    seen=set();silver=[];rejected=[];status=Counter();cent=Decimal('.01')
    for x in sample:
        tid=x.get('trip_id','');reason=[]
        try:t=datetime.fromisoformat(x.get('trip_start_timestamp',''));date_ok=t.year==2025
        except (ValueError,TypeError):t=None;date_ok=False
        numeric_ok=True
        try:
            values={k:num(x.get(k)) for k in ['trip_miles','trip_seconds','fare','tip','additional_charges','trip_total']}
            numeric_ok=all(v is None or v.is_finite() for v in values.values())
        except (InvalidOperation,ValueError):values={};numeric_ok=False
        rules={'trip_required_id_time':len(tid)==40 and date_ok,
               'trip_unique_id':tid not in seen,
               'trip_numeric_format':numeric_ok,
               'trip_distance_duration_range':numeric_ok and all(values.get(k) is None or values[k]>=0 for k in ['trip_miles','trip_seconds']),
               'trip_weight_available':tid in weights}
        for k,ok in rules.items():checks[k]+=1;fails[k]+=not ok
        seen.add(tid)
        if not all(rules.values()):rejected.append(dict(reason=';'.join(k for k,v in rules.items() if not v),payload=x));continue
        u,st=resolve_local(t);w=weathers[0]
        if u is not None:
            w=weather_lookup.get(int(u.replace(minute=0,second=0,microsecond=0).timestamp()),weathers[0])
            if w['weather_sk']==0:st='weather_unavailable'
        status[st]+=1
        def area(role):
            try:v=int(x.get(role+'_community_area',''))
            except (ValueError,TypeError):return 0
            return v if v in areas else 0
        pickup=area('pickup');dropoff=area('dropoff')
        row=dict(trip_token=hmac.new(key,tid.encode(),hashlib.sha256).hexdigest(),
            started_local=t.strftime('%Y-%m-%d %H:%M:%S'),started_utc=u.strftime('%Y-%m-%d %H:%M:%S') if u else None,
            service_date=t.date().isoformat(),hour_24=t.hour,pickup_area_code=pickup,dropoff_area_code=dropoff,
            weather_sk=w['weather_sk'],weather_category=w['weather_category'],weather_status=st,
            trip_seconds=int(values['trip_seconds']) if values['trip_seconds'] is not None else None,
            trip_miles=values['trip_miles'],fare_usd=money(x.get('fare')),tip_usd=money(x.get('tip')),
            additional_charges_usd=money(x.get('additional_charges')),total_usd=money(x.get('trip_total')),
            shared_trip_authorized=int(x.get('shared_trip_authorized',False)),sample_weight=weights[tid],
            sample_month=t.strftime('%Y-%m'),prefix_cluster=tid[:4],sample_batch='A3_20261119')
        silver.append(row)
    silver.sort(key=lambda x:(x['started_local'],x['trip_token']))
    if len(silver)+len(rejected)!=12000:raise ValueError('Row reconciliation failed')
    if sum(x['weather_status']=='matched' for x in silver)/len(silver)<.99:raise ValueError('Weather match rate below 99%')
    if len({x['trip_token'] for x in silver})!=len(silver):raise ValueError('Token collision')
    for k in checks:qc.append(dict(check=k,checked=checks[k],passed=checks[k]-fails[k],failed=fails[k]))
    writecsv(R/'evidence/local/quality_checks.csv',qc)
    dump(R/'data/quarantine/trips.json',rejected);dump(R/'data/quarantine/weather.json',bad_weather)
    writecsv(R/'data/silver/trips_enriched.csv',silver);writecsv(R/'data/silver/weather_hourly.csv',weathers[1:])
    dates=[]
    for i in range(365):
        d=datetime(2025,1,1)+timedelta(days=i)
        dates.append(dict(date_sk=i+1,full_date=d.date().isoformat(),year_num=2025,month_num=d.month,day_of_week=d.weekday()+1,is_weekend=int(d.weekday()>=5)))
    hours=[dict(hour_sk=h+1,hour_24=h,day_part='Morning' if 6<=h<10 else 'Daytime' if 10<=h<16 else 'Evening' if 16<=h<20 else 'Night') for h in range(24)]
    communities=[dict(community_sk=0,community_code=0,community_name='Unknown',ops_zone_tier='Unknown',valid_from='2025-01-01',valid_to=None,is_current=1)]
    for code,name in sorted(areas.items()):
        communities.append(dict(community_sk=code*10+1,community_code=code,community_name=name,ops_zone_tier='Standard',valid_from='2025-01-01',valid_to='2025-07-01' if code==32 else None,is_current=0 if code==32 else 1))
        if code==32:communities.append(dict(community_sk=322,community_code=32,community_name=name,ops_zone_tier='Priority',valid_from='2025-07-01',valid_to=None,is_current=1))
    def ckey(code,day):return 0 if code==0 else code*10+(2 if code==32 and day>='2025-07-01' else 1)
    facts=[];byhour=defaultdict(list);byarea=defaultdict(list)
    for i,x in enumerate(silver,1):
        facts.append(dict(trip_sk=i,trip_token=x['trip_token'],date_sk=(datetime.fromisoformat(x['service_date'])-datetime(2025,1,1)).days+1,
           hour_sk=x['hour_24']+1,pickup_community_sk=ckey(x['pickup_area_code'],x['service_date']),
           dropoff_community_sk=ckey(x['dropoff_area_code'],x['service_date']),weather_sk=x['weather_sk'],
           **{k:x[k] for k in ['trip_seconds','trip_miles','fare_usd','tip_usd','additional_charges_usd','total_usd','shared_trip_authorized','sample_weight','sample_batch']}))
        if x['weather_sk']:byhour[x['weather_sk']].append(x)
        byarea[(x['service_date'],x['pickup_area_code'],x['weather_category'])].append(x)
    gh=[dict(weather_sk=w['weather_sk'],weather_hour_utc=w['weather_hour_utc'],local_hour=w['local_hour'],weather_category=w['weather_category'],**summarize(byhour[w['weather_sk']])) for w in weathers[1:]]
    ga=[dict(service_date=k[0],pickup_area_code=k[1],weather_category=k[2],**summarize(v)) for k,v in sorted(byarea.items())]
    writecsv(R/'data/gold/hourly_weather.csv',gh);writecsv(R/'data/gold/community_daily_weather.csv',ga)
    for name,rows in [('dim_date',dates),('dim_hour',hours),('dim_community',communities),('dim_weather',weathers),('fact_trip',facts)]:writecsv(R/f'data/warehouse/{name}.csv',rows)
    summary=dict(source_sample=12000,accepted_trips=len(silver),quarantined_trips=len(rejected),weather_raw_rows=len(weather_raw),weather_year_hours=len(weathers)-1,
      weather_status=dict(status),gold_hourly_rows=len(gh),gold_community_daily_rows=len(ga),a2_archived_rows=len(readcsv(old)),
      pii_method='HMAC-SHA256 with a local secret key; no raw trip ID or coordinates in Silver/Gold',
      cloud_executed=False,missing_amounts=sum(x['total_usd'] is None for x in silver))
    dump(R/'evidence/local/pipeline_summary.json',summary);print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
