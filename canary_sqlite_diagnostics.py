"""Canary-only, bounded, path-redacted SQLite diagnostics. No business SQL.

Only files under this diagnostic worker's own runtime are observed. The
native connect call, its arguments, return value and exception are preserved.
"""
from pathlib import Path
from collections import deque,OrderedDict
from urllib.parse import urlsplit
from urllib.request import url2pathname
import os,tempfile,threading,hashlib,json,logging,sqlite3,uuid,shutil
LOG=logging.getLogger('canary.sqlite')
_LOCK=threading.RLock();_ROOT=None;_SOURCE=None;_CAPTURES=0;_RENDERS=0;_ROOT_CHANGES=0
_EVENTS=deque(maxlen=64);_PATHS=OrderedDict();_COUNTS={'successful_connects':0,'failed_connects':0,'expected_probe_failures':0};_PROBE=None
_ORIGINAL=getattr(sqlite3,'_canary_native_connect',sqlite3.connect)
SAFE_MESSAGES={'unable to open database file','database is locked','database table is locked','attempt to write a readonly database','disk I/O error','database disk image is malformed','file is not a database','out of memory','unable to open a readonly database'}

def runtime_root():
 global _ROOT,_SOURCE,_CAPTURES
 with _LOCK:
  if _ROOT is None:
   configured=os.environ.get('CANARY_RUNTIME_ROOT')
   _SOURCE='explicit_environment' if configured else 'tempdir_captured_once'
   _ROOT=Path(configured).resolve() if configured else (Path(tempfile.gettempdir())/'wb_canary_synthetic_r12').resolve()
   os.environ['CANARY_RUNTIME_ROOT']=str(_ROOT);_CAPTURES+=1
  return _ROOT

def _id(path):return hashlib.sha256(str(path).encode('utf8')).hexdigest()[:16]

def observe_render(root):
 global _RENDERS,_ROOT_CHANGES
 with _LOCK:
  expected=runtime_root();_RENDERS+=1
  if Path(root).resolve()!=expected:_ROOT_CHANGES+=1;raise RuntimeError('Canary runtime root changed; restart only the diagnostic worker')

def _metadata(database):
 if not isinstance(database,(str,bytes,os.PathLike)):return None
 text=os.fsdecode(database)
 if text==':memory:':return None
 if text.startswith('file:'):
  uri=urlsplit(text)
  if uri.netloc not in ('','localhost'):return None
  text=url2pathname(uri.path)
 path=Path(text).resolve();root=runtime_root()
 if not path.is_relative_to(root):return None
 relative=path.relative_to(root);probe=relative.parts[:1]==('sqlite_probe',)
 if relative.parts[:1]!=('entry',) and not probe:return None
 try:stat=path.stat();exists=True;size=stat.st_size
 except OSError:exists=False;size=None
 return {'root_id':_id(root),'root_characters':len(str(root)),'path_id':_id(path),'path_characters':len(str(path)),'path_utf8_bytes':len(str(path).encode('utf8')),'role':'intentional_probe' if probe else 'staging' if path.suffix=='.tmp' else 'database','exists':exists,'bytes':size,'expected_probe':probe}

def _record(database,error=None):
 meta=_metadata(database)
 if meta is None:return
 with _LOCK:
  key=meta['role']+':'+meta['path_id'];item=_PATHS.pop(key,None) or dict(meta,opens=0,failures=0)
  item['opens']+=1;item['failures']+=int(error is not None);_PATHS[key]=item
  while len(_PATHS)>64:_PATHS.popitem(last=False)
  if error is None:_COUNTS['successful_connects']+=1;return
  _COUNTS['failed_connects']+=1;_COUNTS['expected_probe_failures']+=int(meta['expected_probe'])
  message=str(error);event=dict(meta,exception_type=type(error).__name__,sqlite_errorcode=getattr(error,'sqlite_errorcode',None),sqlite_errorname=getattr(error,'sqlite_errorname',None),message=message if message in SAFE_MESSAGES else '<nonstandard SQLite message redacted>')
  _EVENTS.append(event);LOG.error('CANARY_SQLITE_OPEN %s',json.dumps(event,sort_keys=True))

def _tracked_connect(*args,**kwargs):
 database=args[0] if args else kwargs.get('database')
 try:connection=_ORIGINAL(*args,**kwargs)
 except sqlite3.Error as error:
  try:_record(database,error)
  except Exception as diagnostic_error:LOG.warning('CANARY_SQLITE_DIAGNOSTIC_FAILED %s',type(diagnostic_error).__name__)
  raise
 try:_record(database)
 except Exception as diagnostic_error:LOG.warning('CANARY_SQLITE_DIAGNOSTIC_FAILED %s',type(diagnostic_error).__name__)
 return connection

def install():
 runtime_root()
 with _LOCK:
  if not hasattr(sqlite3,'_canary_native_connect'):sqlite3._canary_native_connect=_ORIGINAL
  sqlite3.connect=_tracked_connect

def summary():
 with _LOCK:
  root=runtime_root()
  return {'root_id':_id(root),'root_characters':len(str(root)),'root_source':_SOURCE,'root_capture_count':_CAPTURES,'full_script_renders':_RENDERS,'root_change_count':_ROOT_CHANGES,'sqlite_version':sqlite3.sqlite_version,**_COUNTS,'observed_paths':list(_PATHS.values()),'errors':list(_EVENTS),'probe_result':_PROBE,'paths_rows_SQL_and_secrets_logged':False}

def probe():
 """Optional once-per-worker synthetic negative test. No existing DB writes."""
 global _PROBE
 with _LOCK:
  if _PROBE is not None:return _PROBE
  parent=runtime_root()/'sqlite_probe';parent.mkdir(exist_ok=True);folder=parent/uuid.uuid4().hex;folder.mkdir();short=folder/'short.sqlite3';long=None
  try:
   connection=_ORIGINAL(short)
   try:connection.execute('CREATE TABLE synthetic_probe(value INTEGER)');connection.execute('INSERT INTO synthetic_probe VALUES(981205)');connection.commit()
   finally:connection.close()
   blob=short.read_bytes();digest=hashlib.sha256(blob).hexdigest();deep=folder
   while len(str(deep/'probe.sqlite3'))<530:deep=deep/'synthetic_path_padding'
   deep.mkdir(parents=True,exist_ok=True);long=deep/'probe.sqlite3';long.write_bytes(blob);out=[]
   for path in (short,long):
    entry={'path_characters':len(str(path)),'bytes_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'same_valid_synthetic_bytes':hashlib.sha256(path.read_bytes()).hexdigest()==digest}
    try:
     connection=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
     try:assert connection.execute('SELECT value FROM synthetic_probe').fetchone()[0]==981205
     finally:connection.close()
     entry['status']='OPENED'
    except sqlite3.Error as error:entry.update(status='EXPECTED_NEGATIVE_PROBE' if path==long else 'SHORT_PATH_FAILURE',exception_type=type(error).__name__,sqlite_errorcode=getattr(error,'sqlite_errorcode',None),sqlite_errorname=getattr(error,'sqlite_errorname',None),message=str(error) if str(error) in SAFE_MESSAGES else '<redacted>')
    out.append(entry)
   _PROBE={'synthetic_only':True,'no_existing_database_changed':True,'sqlite_version':sqlite3.sqlite_version,'results':out,'long_path_result_is_reproduction_not_recovery_of_historical_Cloud_message':True};return _PROBE
  finally:
   # Only this newly-created UUID directory, never the runtime or cache root.
   if folder.resolve().parent!=parent.resolve() or len(folder.name)!=32:raise RuntimeError('Unsafe diagnostic probe cleanup')
   shutil.rmtree(folder)
