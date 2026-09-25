from pathlib import Path
from app.services.asterisk_gateway import _registration_health,mcn_account_transport_health
tid=int(Path("/tmp/boris_mcn_registration_tid").read_text().strip())
print("STRICT_REG",_registration_health(tid))
print("STRICT_TRANSPORT",mcn_account_transport_health("__qa_mcn_registration_runtime_v1"))
