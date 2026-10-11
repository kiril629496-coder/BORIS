#!/usr/bin/env python3
"""Restore an existing canonical DB backup only into a uniquely created temp DB."""
import gzip,hashlib,json,re,secrets,subprocess,tempfile,time
from pathlib import Path
backup=Path("/root/BORIS/backend/backups/schema/boris_db_20261003T171802Z_messenger_v7.sql.gz")
assert backup.is_file() and not backup.is_symlink()
db_name="qa_messenger_restore_"+time.strftime("%Y%m%d_%H%M%S")+"_"+secrets.token_hex(3)
assert re.fullmatch(r"qa_messenger_restore_[0-9_]+[0-9a-f]{6}",db_name)
required=["internal_conversations","internal_conversation_members","internal_messages",
          "internal_message_reactions","internal_message_pins","internal_message_attachments",
          "auth_user_sessions","messenger_legal_acceptances","messenger_security_audit"]
expected={};actual={};created=False;out={"backup":str(backup),"temp_database":db_name}
def psql(sql):
 return subprocess.check_output(["sudo","-u","postgres","psql","-X","-A","-t","-v","ON_ERROR_STOP=1","-d",db_name,"-c",sql],text=True).strip()
try:
 subprocess.run(["sudo","-u","postgres","createdb",db_name],check=True,capture_output=True);created=True
 with tempfile.TemporaryFile() as errors:
  process=subprocess.Popen(["sudo","-u","postgres","psql","-X","-q","-v","ON_ERROR_STOP=1","-d",db_name],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=errors)
  table=None
  try:
   with gzip.open(backup,"rb") as source:
    for line in source:
     if line.startswith(b"COPY public."):
      match=re.match(rb"COPY public\.([a-zA-Z0-9_]+) ",line)
      name=match.group(1).decode() if match else ""
      table=name if name in required or ("audit" in name and "messenger" in name) else None
      if table:expected[table]=0
     elif line==b"\\.\n":table=None
     elif table:expected[table]+=1
     process.stdin.write(line)
  finally:
   process.stdin.close()
  rc=process.wait(timeout=1200)
  if rc:
   errors.seek(0);out["restore_error"]=errors.read(1000).decode(errors="replace").splitlines()[0:2]
   raise RuntimeError("TEMP_RESTORE_FAILED")
 missing=[t for t in required if t not in expected]
 assert not missing,"Missing backup tables: "+",".join(missing)
 for table in expected:
  assert re.fullmatch("[a-zA-Z0-9_]+",table)
  actual[table]=int(psql('SELECT count(*) FROM public."'+table+'"'))
 out.update(status="PASS" if expected==actual else "FAIL",backup_rows=expected,restored_rows=actual,
            receipts_storage="internal_conversation_members.last_read_message_id",
            constraints_unvalidated=int(psql("SELECT count(*) FROM pg_constraint WHERE NOT convalidated")),
            production_database_modified=False)
except Exception as exc:
 out.update(status="FAIL",error=type(exc).__name__+":"+str(exc))
finally:
 if created:
  cleanup=subprocess.run(["sudo","-u","postgres","dropdb",db_name],capture_output=True)
  out["temp_database_removed"]=cleanup.returncode==0
print(json.dumps(out,ensure_ascii=False,indent=2))
raise SystemExit(0 if out.get("status")=="PASS" and out.get("temp_database_removed") else 1)
