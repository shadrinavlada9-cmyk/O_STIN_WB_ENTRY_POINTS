"""Diagnostic entry point. The 40 audited application files are unchanged."""
from pathlib import Path
import os,json,hashlib,tempfile,sys,subprocess,importlib.metadata
import streamlit as st
B=Path(__file__).resolve().parent
R=Path(os.environ.get('CANARY_RUNTIME_ROOT',str(Path(tempfile.gettempdir())/'wb_canary_synthetic_r12')))
R.mkdir(parents=True,exist_ok=True)
os.environ['PUBLIC_SEARCH_ROOT']=str(B/'synthetic/search');os.environ['ENTRY_DATA_ROOT']=str(B/'synthetic/entry');os.environ['ENTRY_PREVIEW']='1'
os.environ['PUBLIC_SEARCH_CACHE']=str(R/'search');os.environ['ENTRY_CACHE_ROOT']=str(R/'entry');os.environ['PUBLIC_XLSX_CACHE']=str(R/'search/xlsx_jobs_r12')
os.environ['WB_SEARCH_DATA_ROOT']=os.environ['WB_ANALIT_HOME']=str(R/'empty');os.environ['TMP']=os.environ['TEMP']=str(R/'os_temp');(R/'os_temp').mkdir(exist_ok=True);tempfile.tempdir=str(R/'os_temp')
import canary_resources
if not hasattr(canary_resources,'MONITOR'):canary_resources.MONITOR=canary_resources.Monitor(R)
monitor=canary_resources.MONITOR
binding=json.loads((B/'canary_audited_code.json').read_text(encoding='utf8'))
for name,h in binding['files'].items():
 if hashlib.sha256((B/name).read_bytes()).hexdigest()!=h:raise RuntimeError('Canary audited-code identity mismatch')
if not getattr(monitor,'identity',None):
 try:
  commit=subprocess.run(['git','rev-parse','HEAD'],cwd=B,capture_output=True,text=True,timeout=5).stdout.strip()
 except (OSError,subprocess.TimeoutExpired):commit='UNAVAILABLE'
 monitor.identity={'source_build':binding['source_build'],'deployed_commit':commit if len(commit)==40 else 'UNAVAILABLE','audited_core_manifest_sha256':hashlib.sha256(json.dumps(binding['files'],sort_keys=True).encode()).hexdigest(),'canary_wrapper_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'synthetic_provenance_sha256':hashlib.sha256((B/'canary_synthetic_provenance.json').read_bytes()).hexdigest(),'python':sys.version,'dependencies':{name:importlib.metadata.version(name) for name in ('streamlit','pandas','numpy','pyarrow','XlsxWriter','psutil')}}
# Start the public Canary in the lightweight resource probe. Explicit UI test
# requests use ?canary_ui=1 only after the actual budget has been observed.
if st.query_params.get('canary_diagnostics')=='1' or st.query_params.get('canary_ui')!='1':
 st.set_page_config(page_title='SYNTHETIC CANARY — resource evidence')
 @st.fragment(run_every=2)
 def diagnostics():
  st.json(monitor.snapshot(),expanded=False)
  st.download_button('Download Canary measurements (JSON)',data=lambda:json.dumps(monitor.snapshot(),ensure_ascii=True,sort_keys=True).encode('utf8'),file_name='canary_measurements.json',mime='application/json',key='canary_diagnostics_download',on_click='ignore')
 diagnostics();st.stop()
# No audit ZIP, review data, original archive, token or production feed is used.
exec(compile((B/'app.py').read_text(encoding='utf8'),str(B/'app.py'),'exec'),{'__name__':'__main__','__file__':str(B/'app.py')})
