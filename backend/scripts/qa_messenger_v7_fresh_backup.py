import gzip,hashlib,json,os,subprocess,time
from pathlib import Path
out=Path("/root/BORIS/backend/backups/schema")/("boris_db_"+time.strftime("%Y%m%dT%H%M%SZ",time.gmtime())+"_messenger_v7.sql.gz")
assert not out.exists()
os.umask(0o077)
p=subprocess.Popen(["sudo","-u","postgres","pg_dump","boris_db"],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
with out.open("xb") as raw:
 with gzip.GzipFile(fileobj=raw,mode="wb",compresslevel=1) as target:
  while True:
   data=p.stdout.read(1024*1024)
   if not data:break
   target.write(data)
error=p.stderr.read();rc=p.wait()
assert rc==0,"pg_dump failed (backup not accepted)"
with gzip.open(out,"rb") as source:
 while source.read(1024*1024):pass
print(json.dumps({"status":"PASS","backup":str(out),"bytes":out.stat().st_size,"sha256":hashlib.sha256(out.read_bytes()).hexdigest(),"older_backups_deleted":0}))
