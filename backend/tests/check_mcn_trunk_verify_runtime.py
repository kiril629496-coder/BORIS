from pathlib import Path
from app.services.asterisk_gateway import _verify_mcn_trunk
from app.services.mcn_core import trunk_list
A="__qa_mcn_registration_runtime_v1"
tid=int(Path("/tmp/boris_mcn_registration_tid").read_text().strip())
rows=trunk_list(A)
assert rows,rows
out=_verify_mcn_trunk(A,tid,rows[0].get("status"))
print("VERIFY_MCN_TRUNK",out)
print("TRUNK_AFTER_VERIFY",trunk_list(A)[0])
