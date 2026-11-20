"""Build the final report from retained local and actual AWS evidence."""
from pathlib import Path
import csv,json,re,textwrap
from PIL import Image,ImageDraw,ImageFont
from docx import Document
from docx.shared import Inches,Pt,RGBColor
from docx.enum.section import WD_SECTION,WD_ORIENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
R=Path(__file__).resolve().parents[1]
def j(p):return json.loads((R/p).read_text())
def csvrows(p):
    with (R/p).open(newline='') as f:return list(csv.DictReader(f))
def diagram():
    W,H=1800,1380;im=Image.new('RGB',(W,H),'white');d=ImageDraw.Draw(im)
    font='/System/Library/Fonts/Supplemental/Arial.ttf'
    f=ImageFont.truetype(font,24);title=ImageFont.truetype(font,30)
    boxes=[(40,100,460,430,'dim_date',['date_sk PK','full_date UNIQUE','year_num, month_num','day_of_week, is_weekend']),
      (40,750,460,1270,'dim_community SCD2',['community_sk PK','community_code BK','community_name','ops_zone_tier','valid_from, valid_to','is_current','Pickup and dropoff roles']),
      (600,160,1190,1200,'fact_trip',['trip_sk PK','trip_token UNIQUE','date_sk FK','hour_sk FK','pickup_community_sk FK','dropoff_community_sk FK','weather_sk FK','trip_seconds, trip_miles','fare_usd, tip_usd','additional_charges_usd','total_usd','shared_trip_authorized','sample_weight','sample_batch','Grain: one accepted trip']),
      (1340,100,1760,430,'dim_hour',['hour_sk PK','hour_24 UNIQUE','day_part']),
      (1320,750,1760,1270,'dim_weather',['weather_sk PK','weather_hour_utc UNIQUE','local_hour','utc_offset_minutes','temperature_c','precipitation_mm','snowfall_cm, wind_kmh','weather_category'])]
    for a,b in [((460,270),(600,400)),((460,930),(600,550)),((460,1000),(600,610)),((1340,270),(1190,460)),((1320,960),(1190,670))]:
        d.line([a,b],fill='#52718c',width=4)
    for x,y,x2,y2,t,lines in boxes:
        d.rectangle([x,y,x2,y2],fill='#f5f7fa',outline='#637181',width=3)
        d.rectangle([x,y,x2,y+65],fill='#dce7f0');d.text((x+16,y+17),t,font=title,fill='black')
        for i,line in enumerate(lines):d.text((x+16,y+85+i*53),line,font=f,fill='black')
    d.text((40,25),'Chicago ride share dimensional model',font=title,fill='black')
    d.text((40,1320),'All dimensions have surrogate keys. The community business key is versioned. Full types are in the SQL DDL.',font=f,fill='black')
    (R/'diagrams').mkdir(exist_ok=True);im.save(R/'diagrams/star_schema.png')
    (R/'diagrams/star_schema.mmd').write_text('''erDiagram
 dim_date ||--o{ fact_trip : date_sk
 dim_hour ||--o{ fact_trip : hour_sk
 dim_community ||--o{ fact_trip : pickup_community_sk
 dim_community ||--o{ fact_trip : dropoff_community_sk
 dim_weather ||--o{ fact_trip : weather_sk
''')
diagram()
doc=Document();sec=doc.sections[0];sec.top_margin=sec.bottom_margin=Inches(.65);sec.left_margin=sec.right_margin=Inches(.7)
for name in ['Normal','Title','Subtitle','Heading 1','Heading 2','Heading 3','Caption','Header']:
    style=doc.styles[name];style.font.name='Arial';style.font.color.rgb=RGBColor(0,0,0)
doc.styles['Normal'].font.size=Pt(10)
doc.styles['Normal'].paragraph_format.space_after=Pt(6)
doc.styles['Normal'].paragraph_format.line_spacing=1.08
doc.styles['Title'].font.size=Pt(24)
doc.styles['Heading 1'].font.size=Pt(16)
doc.styles['Heading 2'].font.size=Pt(12)
header=sec.header.paragraphs[0];header.text='Chicago Rideshare | Assignment 3';header.style='Caption'
footer=sec.footer.paragraphs[0];footer.alignment=2
field=OxmlElement('w:fldSimple');field.set(qn('w:instr'),'PAGE');footer._p.append(field)
def p(t):doc.add_paragraph(t)
def h(t,level=1):doc.add_heading(t,level)
def table(headers,rows,size=9):
    t=doc.add_table(rows=1,cols=len(headers));t.style='Table Grid'
    if headers==['Layer','Implementation']:
        t.autofit=False
        for col,width in zip(t.columns,[1.35,5.75]):col.width=Inches(width)
    for c,v in zip(t.rows[0].cells,headers):c.text=str(v)
    repeat=OxmlElement('w:tblHeader');t.rows[0]._tr.get_or_add_trPr().append(repeat)
    for values in rows:
        for c,v in zip(t.add_row().cells,values):c.text=str(v)
    for ri,row in enumerate(t.rows):
        row._tr.get_or_add_trPr().append(OxmlElement('w:cantSplit'))
        for cell in row.cells:
            pr=cell._tc.get_or_add_tcPr();shade=OxmlElement('w:shd');shade.set(qn('w:fill'),'DCE7F0' if ri==0 else 'FFFFFF' if ri%2 else 'F5F7FA');pr.append(shade)
            borders=OxmlElement('w:tcBorders')
            for edge in ['top','left','bottom','right']:
                e=OxmlElement('w:'+edge);e.set(qn('w:val'),'single');e.set(qn('w:sz'),'4');e.set(qn('w:color'),'D9D9D9');borders.append(e)
            pr.append(borders)
            for para in cell.paragraphs:
                para.paragraph_format.space_after=Pt(3)
                for run in para.runs:run.font.size=Pt(size);run.bold=ri==0
    return t
def wrap_code(line,width=100):
    # Wrap only at whitespace outside quoted literals, preserving SQL strings.
    out=[]
    while len(line)>width:
        quote=None;breaks=[]
        for pos,c in enumerate(line):
            if c in "\"'":
                if quote==c:quote=None
                elif quote is None:quote=c
            if c.isspace() and quote is None and 0<pos<=width:breaks.append(pos)
        if not breaks:break
        cut=breaks[-1];out.append(line[:cut]);line='    '+line[cut:].lstrip()
    return out+[line]
def code(t):
    for line in t.splitlines():
        for chunk in wrap_code(line):
            para=doc.add_paragraph();para.paragraph_format.space_after=Pt(0);para.paragraph_format.line_spacing=1
            r=para.add_run(chunk);r.font.name='Courier New';r.font.size=Pt(8)
def page():doc.add_page_break()

summary=j('evidence/local/pipeline_summary.json');opt=j('evidence/cloud/warehouse/optimization.json')
doc.add_paragraph('Chicago Rideshare Data Warehouse and Query Service',style='Title')
doc.add_paragraph('Assignment 3 | Hanqing Lorina Yang | Cloud Native Data Engineering',style='Subtitle')
p('This project extends the Chicago ride-share domain from Assignments 1 and 2 with weather enrichment, a dimensional MySQL warehouse, and a DynamoDB serving model. Historical reporting belongs in the warehouse; predictable community and time-range reads use precomputed DynamoDB items. The warehouse and serving model were executed in AWS Ohio. The appendices provide executable query logic, complete item-key examples and the original application screenshots.')
p('Execution status as of 19 November 2026: AWS RDS MySQL 8.4.11 contains 12,000 facts and all eight SQL queries ran successfully. DynamoDB contains 35,432 verified items and all six access-pattern queries ran successfully. Eight automated tests passed. Screenshots, machine-readable results and acquisition manifests accompany this report.')
h('Project scope and source continuity')
p('The original A2 snapshot remains unchanged and is separately archived in Bronze. Its 12,000 month-start midnight trips are not pooled with the new probability sample for population estimation. The A3 analytical input retains the same official TNP source, 2025 observation year and community reference, with a new documented sampling design.')
table(['Verified data artifact','Result'],[['A3 selected trips','12,000 unique trips; 1,000 per month'],['Temporal coverage','365 dates and all 24 hours'],['A2 archival rows','12,000, preserved separately'],['Weather records fetched','8,808 UTC hourly records in 13 windows'],['Weather coverage used','8,760 local-year hours'],['Accepted trips and weather matches','12,000 accepted; 11,999 matched; 1 ambiguous local time'],['Missing reported totals','25 retained as NULL'],['Gold outputs','8,760 hourly rows; 7,336 community-day-weather rows']])
h('Sampling and interpretation',2)
p('Each month uses a uniform sample without replacement of 16 of the 65,536 possible four-hex-character trip-ID prefix clusters. All records in those clusters are fetched with stable ordering, offset pagination, and COUNT reconciliation. A further random sample of 1,000 is drawn from that complete monthly pool. Seed 20261119, selected prefixes, raw responses, source-version checks and hashes are retained. The total expansion weight is (65536 / 16) times (monthly pool size / 1000). This is a two-stage probability design, not full-population simple random sampling. Inference must account for clustering. Fixed monthly sample counts cannot demonstrate monthly population demand; estimates are labelled and remain uncertain.')

page();h('Part 1 Live ingestion and Medallion pipeline')
p('Open-Meteo ERA5 historical reanalysis is fetched live for a representative Chicago location at 41.8781, -87.6298. It describes city-level environmental context, not measured conditions at every pickup. Twelve monthly UTC requests plus a 48-hour boundary window produce 8,808 records. Monthly windows are request batching; genuine offset pagination is demonstrated by the TNP acquisition. The latest API response describes historical 2025 weather, not live traffic or real-time weather.')
p('The weather downloader uses requests, network timeouts, bounded retries, Retry-After/backoff, pacing, immutable cached response checks, equal-length arrays and contiguous UTC hours. A missing monthly window blocks publication. The trip downloader verifies a complete candidate pool and a stable source version before sampling.')
table(['Layer','Implementation'],[['Bronze','Original TNP candidate pages, selected snapshots, original A2 CSV, raw weather responses, timestamps and SHA-256 manifests.'],['Silver','Parsed trip values, HMAC tokens, community codes, sampling weights, explicit weather-match status; rejected rows are retained separately.'],['Gold hourly','One row per actual UTC weather hour, including zero-sample hours; counts, expanded estimates and amount summaries.'],['Gold community daily','One row per date, pickup community and weather category for observed sample groups. Missing groups are not population zeroes.']])
h('Quality checks before conformance',2)
qc=csvrows('evidence/local/quality_checks.csv')
table(['Check','Checked','Passed','Failed'],[[x['check'],x['checked'],x['passed'],x['failed']] for x in qc],8)
p('Zero rejected rows is an observed outcome, not a guarantee about future releases. The quarantine path is implemented. Missing financial values remain NULL and are excluded only from the relevant financial denominator; they are not converted to zero or removed from trip counts.')
h('Privacy and time alignment',2)
p('The selected TNP fields contain no rider names, email addresses, payment tokens or coordinates. As a defense-in-depth measure, trip_id is replaced before Silver by HMAC-SHA256 using a local secret key; only Bronze retains the public source identifier. This is pseudonymization rather than a guarantee against linkage. The key is excluded from submission. Rebuilding with another key requires rebuilding all downstream tokens.')
p('Weather is fetched in UTC Unix time and converted with America/Chicago. Temperature and wind are at hour start; precipitation and snowfall use the following timestamp because the API labels their preceding-hour accumulation. TNP local timestamps have no offset: one fall-back-hour trip cannot be resolved uniquely, so its weather foreign key is 0. It remains in total trip counts and is excluded from weather-specific calculations. No arbitrary DST fold is guessed.')
p('Evidence: data/bronze/weather/manifest.json, data/sampling_manifest.json, evidence/local/quality_checks.csv and evidence/local/pipeline_summary.json. These preserve acquisition and pass/fail records; the table above reports observed checks, including zero failures where applicable.')

page();h('Part 2 Dimensional warehouse and SCD2')
p('Fact grain: one accepted sampled TNP trip. The central fact stores reported trip measures, four dimension relationships, and the sampling weight. Pickup and dropoff are separate roles of the same community dimension. Dates and hours also use surrogate keys. Unknown community and weather members preserve referential integrity.')
doc.add_picture(str(R/'diagrams/star_schema.png'),width=Inches(5.9))
p('Full column types, nullability, foreign keys and checks are in warehouse/01_schema.sql. The four dimensions are dim_date, dim_hour, dim_community and dim_weather. Bronze, Silver and Gold staging tables are also defined. Python loads staging inputs; SQL ETL populates all four dimensions and the fact table from Silver data and the community reference.')
h('SCD2 demonstration',2)
p('The internal planning tier for community 32, LOOP, is simulated to change from Standard to Priority on 1 July 2025. This is a teaching scenario, not a factual government or price-policy change. SQL expires the original row and inserts a new surrogate-key row. Fact loading uses valid_from <= service_date < valid_to, with NULL meaning open-ended.')
table(['SK','Tier','Valid from','Valid to exclusive','Current'],[['321','Standard','2025-01-01','2025-07-01','0'],['322','Priority','2025-07-01','NULL','1']])
p('AWS RDS verification found 435 Loop pickup trips linked to version 321 and 472 to version 322; no invalid date-to-version links were found. Both pickup and dropoff mappings pass automated interval checks. The version change does not establish a causal change in fares.')
p('See Appendix D figures D03 and D08 for the two SCD2 rows and historical fact mapping. The saved RDS verification reports zero invalid links; SQL dimension outputs also match independently prepared reference rows.')

page();h('Warehouse analytical queries and optimization')
catalog=[('P2_Q1','Month and weather ROLLUP; sample counts, expanded trip totals and weighted mean amounts'),('P2_Q2','Day type and time-of-day weighted mean distance'),('P2_Q3','Community rain/dry sample counts and weighted mean amounts'),('P2_Q4','Loop historical SCD2 tier and fact-date mapping'),('P2_Q5','Temperature band and weighted tip-to-fare ratio'),('P4_Q1','O Hare July origins joined to destination, date, hour and weather'),('P4_Q2','Monthly expanded estimates with LAG and estimated changes'),('P4_Q3','Historical planning tier and weekend ROLLUP')]
table(['Query','Business analysis','RDS median ms'],[[k,t,opt['baseline_queries'][k]['median_ms']] for k,t in catalog],8)
p('All eight statements executed on AWS RDS MySQL 8.4.11. Each financial mean uses a denominator restricted to non-NULL amounts. P2_Q1 includes grouping flags to distinguish subtotal NULLs. Weather comparisons describe associations in a probability sample; they do not estimate a causal weather effect. Query code is reproduced in the SQL appendix and supplied as an executable file.')
h('Measured optimization with an explicit freshness tradeoff',2)
p(f"On AWS RDS MySQL 8.4.11, {opt['selected_query']} was selected during initial profiling and its output was materialized into a normal MySQL table. The benchmark selected the slowest initial query; rankings may change between runs. Each current timing is the median root-iterator end time across five EXPLAIN ANALYZE runs. The cached result was compared cell-for-cell, independent of row order, against the original query before profiling the read.")
table(['AWS RDS metric','Observed value'],[['Original median root end',f"{opt['before']['median_ms']} ms"],['Cached SELECT median root end',f"{opt['after']['median_ms']} ms"],['Initial materialization wall time',f"{opt['materialization_wall_ms']:.3f} ms"],['Result equality','Verified'],['Deployment','AWS RDS MySQL 8.4.11']])
p('The benefit applies to repeated reads of a fixed snapshot. Building or refreshing the table costs additional work and storage; changes to the underlying fact or dimension tables make the cache stale. The cached SELECT time is not an end-to-end pipeline time. The screenshot runs show 87.2 ms before and 0.141 ms after; those individual observations are separate from the five-run medians above. The comparison verifies identical row values without ordering. The cached SELECT omits ORDER BY, so consumers needing ranked output must add ORDER BY sample_trips DESC and remeasure that ordered query.')
p('Appendix D contains the five Part 2 result screenshots and the two EXPLAIN ANALYZE screenshots. Full plans, five-run timings, refresh SQL and equality evidence are retained in evidence/cloud/warehouse/.')

page();h('Part 3 DynamoDB serving model')
p('DynamoDB is selected for predictable community-and-time retrieval and precomputed hourly summaries. Partitioning by community and month bounds each access scope; sort keys order trips and summary items. A secondary index provides the reverse dropoff access path. MongoDB would be appropriate for flexible nested trip documents, but flexible document searches are not the serving requirements here. Neo4j would suit multi-hop relationships; this source lacks persistent rider or driver identities and the selected queries do not require graph traversal. These alternatives are less aligned with the chosen workloads, not inherently unsuitable for ride-share systems.')
table(['Access pattern','Key design'],[['Pickup community in a month/time range','PK = PICKUP#area#YYYY-MM; SK = TRIP#local_timestamp#token'],['Stored community hourly summaries','Same pickup PK; SK = HOUR#local_hour'],['Dropoff community in a month/time range','GSI1PK = DROPOFF#area#YYYY-MM; GSI1SK = trip SK'],['Optional token-only lookup','PK = TRIP#token; SK = LOOKUP, pointing to the base item']])
p('Verified AWS load: 35,432 items, comprising 12,000 trip items, 12,000 lookup pointers and 11,432 community-hour summaries. Table RideshareTripsA3 is in us-east-2. Only trip items have GSI1 keys, making the index sparse. The collection contains the whole accepted sample; displayed examples are not the entire load. On-demand billing is configured, with no claim that production operation is free.')
p('All Query calls follow LastEvaluatedKey, including an empty filtered page, and record consumed capacity and actual client elapsed time. GSI reads are eventually consistent. The loader validates all base-table keys using a strongly consistent paginated scan after loading. Hour summaries describe sampled trips; a missing summary is not proof of zero population demand.')
table(['Part 3 query','Purpose'],[['P3_Q1','Pickup trips for one community and day'],['P3_Q2','Precomputed hourly summaries for that community and day'],['P3_Q3','Arrivals to a dropoff community in the chosen month through GSI1']])
ddb_results={name:j('evidence/cloud/dynamodb/'+name+'.json') for name in ['P3_Q1','P3_Q2','P3_Q3','P4_Q1','P4_Q2','P4_Q3']}
table(['AWS query','Items / pages','Client ms','Consumed capacity'],[[name,str(v['count'])+' / '+str(v['pages']),v['client_elapsed_ms'],v['consumed_capacity_units']] for name,v in ddb_results.items()],8)
p('These are actual SDK responses, not local simulation. Times are single client observations including network and setup effects, not service latency guarantees. Complete responses and exact parameters are saved in evidence/cloud/dynamodb/. Independent filters over the local input reproduce all six result sets.')
p('Appendix D shows the Active on-demand table, Active GSI1, loaded items and all six live query outputs. The console overview still showed zero items at capture time while Explore items displayed records; the loader strong scan verified 35,432 keys. Metadata item counts are periodically updated and are not the load-validation method.')

page();h('Part 4 Query showcase and platform reflection')
table(['Store','Showcase query','Natural strength'],[['MySQL P4_Q1','Origins to destinations by time and weather','Ad hoc joins across multiple dimensions'],['MySQL P4_Q2','Weighted monthly estimates with LAG','Historical ordering and window analysis'],['MySQL P4_Q3','Tier and weekend subtotals','SCD2 dimension joins and ROLLUP'],['DynamoDB P4_Q1','Evening pickup trip details','Partition and sort-key range'],['DynamoDB P4_Q2','One month of hourly summaries','Precomputed serving items without joins'],['DynamoDB P4_Q3','Projected arrival fields via GSI1','Alternate indexed access path']])
h('Complementary architecture reflection',2)
p('The warehouse answers questions whose dimensions and aggregation levels change. P4_Q1 combines origin, destination, calendar, time of day and weather; P4_Q2 computes an ordered comparison of weighted monthly estimates; P4_Q3 preserves the planning tier that was valid when each trip occurred. The relational model makes these relationships and historical semantics explicit. On RDS, these queries returned 27, 12 and 10 rows respectively. Their median EXPLAIN ANALYZE root-end times were 3.18, 27.6 and 60.9 ms; the screenshots and saved results establish execution on the deployed warehouse.')
p('The DynamoDB design serves a different workload. In AWS, P4_Q1 returned 10 evening records, P4_Q2 returned 129 precomputed hourly items for a month, and P4_Q3 returned four projected arrivals through the dropoff index. These operations avoid recomputing reporting joins at read time. MySQL could also serve preaggregated tables, so the advantage is the chosen keyed serving design rather than a claim that relational databases cannot do these reads. The preceding table records observed client times and capacity; small single-run timings do not establish production throughput.')
p('Both stores are derived from the same validated Silver sample and carry a common sample batch. The warehouse remains the source for flexible historical analysis; DynamoDB is a reproducible serving projection. A future refresh must publish matching batches, rebuild aggregates and invalidate stale materialized results. This coursework release is a historical batch, so neither store makes the source data real-time. Sample uncertainty, city-level weather resolution and ambiguous local times remain visible in the outputs.')
p('Matching RDS and DynamoDB screenshots are in Appendix D. SQL root-iterator timings and SDK client elapsed times measure different boundaries and should not be treated as a controlled speed comparison between engines.')

page();h('Part 5 Production architecture judgment')
p('The following judgment uses the deployed configurations, actual Part 4 queries and the November 2026 account bill captured on 19 November. Figures D24, D25 and D27 provide the configuration and billing evidence. Historical A1 and A2 calculator scenarios are background, not verified prices for this combined workload.')
judgment='With a $150 monthly AWS budget, I would prioritize RDS for this workload. P4_Q1 joins five dimensional roles, P4_Q2 calculates monthly changes, and P4_Q3 combines historical tiers with weekend subtotals. Their measured RDS median times were 3.18, 27.6 and 60.9 ms. The console shows a Single-AZ db.t4g.micro with 20 GiB gp2 storage. November account billing shows $8.25 of RDS charges in Virginia and $5.48 in Ohio, offset by a $13.73 negative adjustment; the free-plan banner states that credits cover costs. Therefore, the $0 net bill is neither zero resource consumption nor a production monthly forecast. These charges are account-wide and not attributable solely to Assignment 3. DynamoDB is on-demand, but its charges are absent from the captured bill. P4_Q2 returned 129 hourly items and P4_Q3 returned four projected arrivals, demonstrating useful serving access patterns. For modest traffic, I would initially serve summaries from MySQL and retain DynamoDB only when measured lookup volume justifies duplication. I would reserve budget for backups, monitoring, refreshes and growth, then validate monthly affordability using representative traffic and current regional prices before production deployment.'
p(judgment)
h('Evidence and reproducibility checklist',2)
table(['Required evidence','Current state'],[['Live API responses and request manifests','Completed locally'],['QC pass/fail and quarantines','Completed locally'],['Star schema, SCD2 and all SQL','Executed and verified on RDS'],['Cloud RDS execution and optimization','Completed; results equal without row ordering'],['DynamoDB deployment and six queries','Completed and verified in AWS'],['Current service tiers and billing','Console screenshots saved; credits distinguished'],['Query screenshots and production judgment','Included with measured results and limitations']])
p('Submission artifacts: final report PDF or Word; pipeline and transformation Python files; warehouse SQL DDL, ETL and queries; DynamoDB creation/loading/query code. Include requirements, source links, manifests and diagrams as supporting files. Do not submit .env, the token key, virtual environments or credentials. The report must contain real screenshots rather than reconstructed result grids presented as application evidence.')

page();h('Appendix A SQL business questions and statements')
text=(R/'warehouse/03_queries.sql').read_text()
for block in text.split('-- QUERY ')[1:]:
    start=len(doc.paragraphs)
    name,body=block.split('\n',1);h(name.strip(),2)
    lines=body.splitlines();p(lines[0].removeprefix('-- Business question: '));code('\n'.join(lines[1:]).strip())
    p('Live result: Appendix D, '+{'P2_Q1':'D05','P2_Q2':'D06','P2_Q3':'D07','P2_Q4':'D08','P2_Q5':'D09','P4_Q1':'D12','P4_Q2':'D13','P4_Q3':'D14'}[name.strip()]+'.')
    for para in doc.paragraphs[start:-1]:para.paragraph_format.keep_with_next=True

page();h('Appendix B Exact DynamoDB item examples')
p('These are 10 examples from the loaded input; a strongly consistent AWS scan verified every expected primary key after loading. GSI1SK equals the full SK shown for each item. The loaded-items console screenshot is figure D17. Line breaks split keys for display only and are not part of their values.')
examples=csvrows('evidence/local/dynamodb_item_examples.csv')[:10]
for offset in [0,5]:
    if offset:page();h('Appendix B Item examples continued')
    values=[]
    for idx,x in enumerate(examples[offset:offset+5],offset+1):
        keys='\n'.join(k+' = '+x[k] for k in ['PK','SK','GSI1PK','GSI1SK'])
        attrs='Time: '+x['started_local']+'\nPickup: '+x['pickup_area_code']+'\nDropoff: '+x['dropoff_area_code']+'\nWeather: '+x['weather_category']+'\nTotal USD: '+x['total_usd']
        values.append([idx,keys,attrs])
    t=table(['Item','PK / SK / GSI keys','Five attributes'],values,8)
    t.autofit=False
    for column,width in zip(t.columns,[.4,4.7,2.0]):column.width=Inches(width)
    for row in t.rows:
        for cell,width in zip(row.cells,[.4,4.7,2.0]):cell.width=Inches(width)

page();h('Appendix C Executed DynamoDB query code')
p('The shared paginate helper in dynamodb/04_dynamodb.py executes table.query, follows every LastEvaluatedKey, and returns all items, capacity and client time. These are the six executed access patterns. Each API result file preserves the exact parameters and observation time. The corresponding live output screenshots are included in Appendix D.')
params=j('data/dynamodb/query_parameters.json')
code('p = '+repr({k:params[k] for k in ['pickup_pk','dropoff_pk','day','month']}))
expressions=[
 ('P3_Q1',"Key('PK').eq(p['pickup_pk']) &\n    Key('SK').between('TRIP#'+p['day']+'T00:00',\n                      'TRIP#'+p['day']+'T23:59:59~')",''),
 ('P3_Q2',"Key('PK').eq(p['pickup_pk']) &\n    Key('SK').begins_with('HOUR#'+p['day'])",''),
 ('P3_Q3',"Key('GSI1PK').eq(p['dropoff_pk']) &\n    Key('GSI1SK').begins_with('TRIP#'+p['month'])","    IndexName='GSI1',\n"),
 ('P4_Q1',"Key('PK').eq(p['pickup_pk']) &\n    Key('SK').between('TRIP#'+p['day']+'T16:00',\n                      'TRIP#'+p['day']+'T23:59:59~')",''),
 ('P4_Q2',"Key('PK').eq(p['pickup_pk']) &\n    Key('SK').begins_with('HOUR#'+p['month'])",''),
 ('P4_Q3',"Key('GSI1PK').eq(p['dropoff_pk']) &\n    Key('GSI1SK').between('TRIP#'+p['day']+'T00:00',\n                         'TRIP#'+p['day']+'T23:59:59~')","    IndexName='GSI1',\n    ProjectionExpression='started_local,total_usd,weather_category',\n")]
for idx,(name,expression,extra) in enumerate(expressions):
    if idx==3:page();h('Appendix C Part 4 query code')
    start=len(doc.paragraphs);h(name,2);p(ddb_results[name]['business_question'])
    code('result = paginate(\n    table,\n'+extra+'    KeyConditionExpression=(\n    '+expression+'\n    )\n)')
    p('Live result: Appendix D, '+{'P3_Q1':'D18','P3_Q2':'D19','P3_Q3':'D20','P4_Q1':'D21','P4_Q2':'D22','P4_Q3':'D23'}[name]+'.')
    for para in doc.paragraphs[start:-1]:para.paragraph_format.keep_with_next=True

page();h('Sources and implementation references')
sources=[('Chicago Transportation Network Providers Trips 2025 onward','https://data.cityofchicago.org/d/6dvr-xwnh'),('Chicago Community Areas','https://data.cityofchicago.org/d/igwz-8jzy'),('Open Meteo historical API and time conventions','https://open-meteo.com/en/docs/historical-weather-api'),('Socrata pagination','https://dev.socrata.com/docs/queries/offset.html'),('MySQL EXPLAIN ANALYZE','https://dev.mysql.com/doc/refman/8.4/en/explain.html'),('MySQL ROLLUP','https://dev.mysql.com/doc/refman/8.4/en/group-by-modifiers.html'),('DynamoDB query pagination','https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Query.Pagination.html'),('DynamoDB on demand capacity','https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/on-demand-capacity-mode.html'),('RDS TLS','https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html'),('DynamoDB metadata item count update interval','https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_TableDescription.html')]
for title,url in sources:p(title+'\n'+url)
p('Retrieval evidence is stored with each original API response. TNP metadata supplies its terms-of-use label; no Creative Commons license is asserted for Chicago trip data. Open-Meteo attribution and upstream reanalysis provenance should be retained with any published derivative. Exact acquisition timestamps, content hashes and selected prefixes are in the supplied manifests.')
# Retain original screenshots unchanged; landscape pages preserve legibility.
section=doc.add_section(WD_SECTION.NEW_PAGE)
section.orientation=WD_ORIENT.LANDSCAPE
section.page_width=Inches(11);section.page_height=Inches(8.5)
section.top_margin=section.bottom_margin=Inches(.45)
section.left_margin=section.right_margin=Inches(.5)
shots=R/'evidence/cloud/screenshots'
labels={
 '01':'RDS instance status', '03':'SCD2 version rows',
 '05':'RDS P2 Q1 month and weather rollup','06':'RDS P2 Q2 day type and distance',
 '07':'RDS P2 Q3 community and weather','08':'RDS P2 Q4 historical fact mapping',
 '09':'RDS P2 Q5 temperature and tip ratio','10':'Original query execution plan',
 '11':'Materialized result execution plan','12':'RDS P4 Q1 origin destination showcase',
 '13':'RDS P4 Q2 monthly change showcase','14':'RDS P4 Q3 tier and weekend showcase',
 '15':'DynamoDB table and on demand mode','16':'DynamoDB secondary index',
 '17':'DynamoDB loaded items','24':'RDS instance and storage configuration',
 '25':'November account bill summary','26':'Account service charge summary',
 '27':'RDS regional charges and negative adjustment'}
for n,(prefix,label) in enumerate(labels.items()):
    if n:page()
    h('Appendix D  Figure D'+prefix+'  '+label)
    img=next(shots.glob(prefix+'_*.png'))
    para=doc.add_paragraph();para.paragraph_format.space_after=Pt(0)
    para.add_run().add_picture(str(img),width=Inches(9.65))
    if prefix=='10':p('Single screenshot run: root end 87.2 ms. The five-run original median is 71.9 ms.')
    if prefix=='11':p('Single screenshot run: root end 0.141 ms. The five-run cached median is 0.12 ms; refresh cost is separate.')
    if prefix=='27':p('Account-to-date RDS: Virginia $8.25 + Ohio $5.48, offset by the No Region negative adjustment of $13.73. Net $0.00.')
for group in [(18,19),(20,21),(22,23)]:
    page();h('Appendix D  Live DynamoDB query results')
    for num in group:
        img=next(shots.glob(str(num)+'_dynamodb_*.png'))
        h('Figure D'+str(num)+'  '+img.stem.split('dynamodb_')[1].replace('_',' '),2)
        para=doc.add_paragraph();para.paragraph_format.space_after=Pt(6)
        para.add_run().add_picture(str(img),width=Inches(9.0 if num >= 22 else 9.4))
p('GPT-6 assisted with this assignment.')
# Remove inherited decorative paragraph borders from the default template.
for root in [doc._element,doc.styles.element]:
    for border in list(root.iter(qn('w:pBdr'))):border.getparent().remove(border)
for run in header.runs:run.font.color.rgb=RGBColor(0,0,0)
(R/'report').mkdir(exist_ok=True);doc.save(R/'report/Assignment3_Report.docx')
print('Final report generated from AWS evidence. Part 5 word count:',len(judgment.split()))
