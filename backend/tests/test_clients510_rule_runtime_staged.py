"""Exercise the exact staged runtime in non-root private PostgreSQL."""
import importlib.util
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile


def test_staged_rule_runtime_real_postgresql():
    spec=importlib.util.find_spec('app')
    assert spec and spec.submodule_search_locations, 'Staged app package unavailable'
    backend=Path(next(iter(spec.submodule_search_locations))).resolve().parent
    assert (backend/'app/services/control_rule_runtime.py').is_file()
    testdir=Path(__file__).resolve().parent
    # The canonical certifier runs as root. Only this test subprocess drops to
    # the already existing QA account; the full suite is never skipped.
    qa=pwd.getpwnam('sentinelx') if os.geteuid()==0 else pwd.getpwuid(os.geteuid())
    assert qa.pw_uid!=0, 'Private PostgreSQL must run without root privileges'
    with tempfile.TemporaryDirectory(prefix='boris-rule-test-results-') as folder:
        os.chmod(folder,0o700)
        identity={}
        if os.geteuid()==0:
            os.chown(folder,qa.pw_uid,qa.pw_gid)
            identity={'user':qa.pw_uid,'group':qa.pw_gid,'extra_groups':[]}
        # Do not inherit production DSNs, API credentials, startup dotenv paths
        # or Python startup hooks from the certifier process.
        env={'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
             'PYTHONPATH':str(backend),'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1'}
        result=subprocess.run([sys.executable,str(testdir/'run_private_postgres_tests.py'),
            '--backend',str(backend),'--python',sys.executable,'--output',folder],
            cwd=str(backend),env=env,**identity,capture_output=True,text=True,timeout=240)
        reportpath=Path(folder)/'result.json'
        assert reportpath.exists(), result.stdout[-5000:]+result.stderr[-1000:]
        report=json.loads(reportpath.read_text())
        print('PRIVATE_POSTGRES_RESULT='+json.dumps(report,sort_keys=True))
        assert result.returncode==0, result.stdout[-5000:]+result.stderr[-1000:]
        assert report['effective_uid']==qa.pw_uid and report['effective_uid']!=0
        assert report['cluster_stopped'] and report['private_data_removed']
        assert report['production_db_access'] is False and report['tcp_listener'] is False
