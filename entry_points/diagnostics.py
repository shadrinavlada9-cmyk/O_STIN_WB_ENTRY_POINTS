"""Server-side structural tracebacks. Never log exception values, frames' locals or HTTP bodies."""
from pathlib import Path
import contextvars,datetime,json,logging,os,re,subprocess,traceback
_stage=contextvars.ContextVar('wb_operation_stage',default='app.start')
_snapshot=contextvars.ContextVar('wb_snapshot_sha',default=None)
LOG=logging.getLogger('wb.cloud.diagnostics')
def stage(name,sha=None):
 _stage.set(name)
 if sha is not None:_snapshot.set(sha)
def identity():
 value=os.environ.get('STREAMLIT_GIT_COMMIT') or os.environ.get('COMMIT_SHA')
 if value and re.fullmatch('[0-9a-f]{40}',value):return value
 try:
  p=subprocess.run(['git','-C',str(Path(__file__).resolve().parent),'rev-parse','HEAD'],capture_output=True,text=True,timeout=2)
  if p.returncode==0 and re.fullmatch('[0-9a-f]{40}',p.stdout.strip()):return p.stdout.strip()
 except (OSError,subprocess.TimeoutExpired):pass
 return 'UNAVAILABLE'
def artifact(path):
 if path is None:return None
 p=Path(path)
 parts=p.parts
 for name in ('analytics','state','native','ui_app','wb_search_r32'):
  if name in parts:return '/'.join(parts[parts.index(name):])
 # Do not expose arbitrary source filenames, personal paths or request parameters.
 return p.name if re.fullmatch(r'[a-zA-Z0-9_.-]+\.(json|parquet|xlsx|sqlite3|py)',p.name) else '<redacted-path>'
def error(exc,operation=None,sha=None,path=None,category=None):
 category=category or ('RESOURCE_EXHAUSTION' if isinstance(exc,MemoryError) or isinstance(exc,OSError) and getattr(exc,'errno',None)==12 else 'CACHE_CORRUPT' if type(exc).__name__=='CacheIntegrityError' else 'MISSING_ARTIFACT' if isinstance(exc,FileNotFoundError) else 'READ_ERROR' if isinstance(exc,OSError) else 'OPERATION_FAILED')
 chain=[];seen=set();current=exc
 while current is not None and id(current) not in seen:
  seen.add(id(current))
  frames=traceback.extract_tb(current.__traceback__) if current.__traceback__ else traceback.extract_stack()[:-1]
  chain.append({'type':type(current).__name__,'traceback':[{'file':artifact(f.filename),'line':f.lineno,'function':f.name} for f in frames]})
  current=current.__cause__ or (None if current.__suppress_context__ else current.__context__)
 snapshot=sha or _snapshot.get()
 snapshot=snapshot if isinstance(snapshot,str) and re.fullmatch('[0-9a-f]{64}',snapshot) else None
 event={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'stage':operation or _stage.get(),'deployed_commit':identity(),'snapshot_sha256':snapshot,'category':category,'artifact':artifact(path or getattr(exc,'filename',None)),'exception_chain':chain}
 LOG.error('WB_CLOUD_EVENT %s',json.dumps(event,ensure_ascii=True))
 return event
