"""Package an explicit allowlist; never include local credentials or private keys."""
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
import hashlib,json,re
R=Path(__file__).resolve().parents[1]

def safe(p):
    rel=p.relative_to(R)
    return p.is_file() and not any(x in ('.private','.git','__pycache__','.venv') for x in rel.parts) and p.name not in ('.env','填写数据库密码.txt','.DS_Store') and not p.name.endswith('.pyc') and 'DRAFT' not in p.name

def write_zip(name,paths):
    selected=sorted({p for p in paths if safe(p)})
    # Check common credential signatures without printing matching content.
    forbidden=re.compile(rb'(?:AKIA|ASIA)[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
    for p in selected:
        if p.suffix.lower() in ('.py','.md','.json','.csv','.sql','.txt'):
            if forbidden.search(p.read_bytes()):raise RuntimeError('Credential signature detected in '+str(p.relative_to(R)))
    with ZipFile(R/name,'w',ZIP_DEFLATED,compresslevel=6) as z:
        for p in selected:z.write(p,'Assignment3/'+str(p.relative_to(R)))
    with ZipFile(R/name) as z:
        assert z.testzip() is None
        assert all('/.private/' not in n and not n.endswith('/.env') and '填写数据库密码' not in n for n in z.namelist())
    return dict(file=name,files=len(selected),bytes=(R/name).stat().st_size,sha256=hashlib.sha256((R/name).read_bytes()).hexdigest())

def main():
    data=list((R/'data').rglob('*'))
    core=[]
    for directory in ('scripts','warehouse','dynamodb','tests','docs','diagrams','evidence'):
        core.extend((R/directory).rglob('*'))
    core.extend(R/f for f in ('README.md','提交说明.md','requirements.txt','.env.example','report/Assignment3_Report.docx'))
    records=[write_zip('Assignment3_Submission.zip',core),write_zip('Assignment3_Data.zip',data)]
    (R/'SUBMISSION_MANIFEST.json').write_text(json.dumps(records,indent=2))
    for record in records:print(record['file'],record['files'],'files',round(record['bytes']/1024/1024,2),'MiB')
if __name__=='__main__':main()
