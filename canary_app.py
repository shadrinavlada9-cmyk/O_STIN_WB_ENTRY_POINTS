"""Diagnostic entry point. The 40 audited application files are unchanged."""
from pathlib import Path
import os,json,hashlib,tempfile,sys
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
# Start the public Canary in the lightweight resource probe. Explicit UI test
# requests use ?canary_ui=1 only after the actual budget has been observed.
if st.query_params.get('canary_diagnostics')=='1' or st.query_params.get('canary_ui')!='1':
 st.set_page_config(page_title='SYNTHETIC CANARY — resource evidence')
 @st.fragment(run_every=2)
 def diagnostics():st.json(monitor.snapshot(),expanded=False)
 diagnostics();st.stop()
# No audit ZIP, review data, original archive, token or production feed is used.
binding=json.loads((B/'canary_audited_code.json').read_text(encoding='utf8'))
for name,h in binding['files'].items():
 if hashlib.sha256((B/name).read_bytes()).hexdigest()!=h:raise RuntimeError('Canary audited-code identity mismatch')
exec(compile((B/'app.py').read_text(encoding='utf8'),str(B/'app.py'),'exec'),{'__name__':'__main__','__file__':str(B/'app.py')})
