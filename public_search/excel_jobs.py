"""Bounded disk-only workbook jobs. No Streamlit, credentials or WB writes."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import contextlib,threading,time,json,hashlib,uuid,os,tempfile,zipfile,shutil,dataclasses,functools
from collections import OrderedDict
from .excel_job_locks import locked
import psutil
VERSION='PUBLIC_XLSX_JOB_R12_1'
MAX_FILE=512*1024**2

def digest(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
 return h.hexdigest()

def atomic_json(path,value):
 temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
 try:
  with temp.open('w',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False,sort_keys=True);f.flush();os.fsync(f.fileno())
  deadline=time.monotonic()+3
  while True:
   try:os.replace(temp,path);break
   except PermissionError:
    if time.monotonic()>=deadline:raise
    time.sleep(.01)
 finally:temp.unlink(missing_ok=True)

@functools.lru_cache(maxsize=1)
def generator_version():
 root=Path(__file__).resolve().parents[1]
 paths=sorted(p for folder in ('public_search','wb_search_r32','entry_points') for p in (root/folder).glob('*.py'))
 return hashlib.sha256((VERSION+''.join(digest(p) for p in paths)).encode()).hexdigest()

def identity(request,context):
 """Content, order, columns and all public selection parameters; no disk paths."""
 from wb_search_r32.export_ticket import Spool,Chunked
 from wb_search_r32.mode_views import Anomalies
 def item(value):
  if isinstance(value,Spool):return {'rows':len(value),'columns':value.columns,'sha256':digest(value.path)}
  if isinstance(value,Chunked):return {'columns':value.columns,'chunks':[item(v) for v in value.chunks]}
  if isinstance(value,Anomalies):return {'deferred':'accepted_anomaly_rows','selection':{k:v for k,v in dataclasses.asdict(value).items() if k!='root'}}
  raise ValueError('Excel jobs require immutable spooled inputs')
 if dataclasses.is_dataclass(request):
  payload={'type':type(request).__module__+'.'+type(request).__qualname__,'parameters':{k:v for k,v in dataclasses.asdict(request).items() if k not in ('root','url','cache')}}
 else:payload=[(key,item(request[key])) for key in request]
 value={'generator':generator_version(),'context':context,'input':payload}
 return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()

def inspect_workbook(path):
 from lxml import etree as ET
 ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
 with zipfile.ZipFile(path) as z:
  if z.testzip() is not None:raise ValueError('Invalid workbook CRC')
  book=ET.fromstring(z.read('xl/workbook.xml'));names=[x.attrib['name'] for x in book.findall('s:sheets/s:sheet',ns)]
  sheets=[]
  for n,name in enumerate(names,1):
   count=0;dimension=None;auto=None
   with z.open('xl/worksheets/sheet'+str(n)+'.xml') as f:
    prefix='{'+ns['s']+'}'
    for event,el in ET.iterparse(f,events=('end',),tag=tuple(prefix+t for t in ('row','dimension','autoFilter')),resolve_entities=False,no_network=True):
     tag=el.tag.rsplit('}',1)[-1]
     if tag=='row':count+=1
     elif tag=='dimension':dimension=el.attrib.get('ref')
     elif tag=='autoFilter':auto=el.attrib.get('ref')
     el.clear()
     while el.getprevious() is not None:del el.getparent()[0]
   if count>1048576:raise ValueError('Excel worksheet row overflow')
   sheets.append({'name':name,'rows_including_header':count,'dimension':dimension,'autoFilter':auto})
 return sheets

class Jobs:
 def __init__(self,root,max_bytes=2*1024**3,ttl=86400,max_pending=3,lease=600):
  self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
  self.max_bytes=max_bytes;self.reserve=min(MAX_FILE,max_bytes//2);self.ttl=ttl;self.lease=lease;self.max_pending=max_pending
  self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='public-xlsx');self.guard=threading.RLock();self.futures={};self.starts={};self.boot=uuid.uuid4().hex;self.closed=threading.Event()
  self.pid=os.getpid();self.process_started=psutil.Process().create_time()
  self.hot=OrderedDict();self.hot_bytes=0;self.hot_limit=192*1024**2
  self.heartbeat=threading.Thread(target=self._pulse,daemon=True);self.heartbeat.start()
 def directory(self,key):
  if len(key)!=64 or any(c not in '0123456789abcdef' for c in key):raise ValueError('Invalid job key')
  p=self.root/key
  if p.is_symlink() or p.resolve().parent!=self.root:raise ValueError('Invalid cache directory')
  return p
 def _read(self,key):
  try:
   p=self.directory(key)/'state.json'
   if p.stat().st_size>1024*1024:return {'state':'INVALID'}
   r=json.loads(p.read_text(encoding='utf8'))
   if not isinstance(r,dict) or r.get('key')!=key or r.get('format')!=VERSION or r.get('state') not in ('QUEUED','RUNNING','READY','ERROR'):return {'state':'INVALID'}
   if r.get('generator')!=generator_version():return {'state':'INVALID'}
   if not isinstance(r.get('heartbeat'),(int,float)):return {'state':'INVALID'}
   if r['state']=='READY' and (type(r.get('bytes')) is not int or not 0<r['bytes']<=MAX_FILE or not isinstance(r.get('sha256'),str) or len(r['sha256'])!=64 or not isinstance(r.get('sheets'),list)):return {'state':'INVALID'}
   if r['state']=='READY' and any(not isinstance(r.get(field),(int,float)) for field in ('started','finished','created','lease_until')):return {'state':'INVALID'}
   return r
  except (OSError,ValueError,TypeError):return {'state':'INVALID' if (self.directory(key)/'state.json').exists() else 'MISSING'}
 def _lease(self,key):
  try:
   p=self.directory(key)/'owner.json'
   if p.stat().st_size>8192:return {}
   value=json.loads(p.read_text(encoding='utf8'))
   return value if isinstance(value,dict) and value.get('key')==key else {}
  except (OSError,ValueError,TypeError):return {}
 def _alive(self,key,record):
  if record.get('owner')==self.boot:
   with self.guard:
    future=self.futures.get(key)
    return future is not None and not future.done()
  pid=record.get('pid');started=record.get('process_started')
  if type(pid) is not int or not isinstance(started,(int,float)):
   return time.time()-record.get('heartbeat',0)<=90
  try:
   process=psutil.Process(pid)
   return abs(process.create_time()-started)<.01 and process.is_running() and process.status()!=psutil.STATUS_ZOMBIE
  except (psutil.NoSuchProcess,psutil.ZombieProcess):return False
  except psutil.AccessDenied:return True
 def status(self,key,verify=False):
  r=self._read(key)
  if r['state'] in ('QUEUED','RUNNING') and not self._alive(key,self._lease(key) or r):return dict(r,state='INTERRUPTED')
  if r['state']=='READY' and verify:
   try:
    with locked(self.root,key,timeout=.1):
     r=self._read(key);p=self.directory(key)/'artifact.xlsx'
     if r.get('state')!='READY' or p.is_symlink() or p.stat().st_size!=r.get('bytes') or digest(p)!=r.get('sha256'):return {'state':'INVALID'}
     r['lease_until']=time.time()+self.lease;atomic_json(self.directory(key)/'state.json',r)
   except TimeoutError:return {'state':'BUSY'}
   except (OSError,ValueError):return {'state':'INVALID'}
  return r
 def _pulse(self):
  while not self.closed.wait(3):
   with self.guard:keys=list(self.futures)
   for key in keys:
    # Metadata writes are independent of workbook publication; serialize them
    # with the worker, not the long-lived per-job generation lock.
    with self.guard:
     r=self._read(key)
     if r.get('owner')==self.boot and r['state'] in ('QUEUED','RUNNING'):
      r['heartbeat']=time.time();atomic_json(self.directory(key)/'state.json',r)
 def submit(self,key,builder,defer_start=False,filename='Search.xlsx'):
  with self.guard:
   if key in self.futures and not self.futures[key].done() and self._read(key)['state']!='ERROR':return self.status(key)
   state=self.status(key,verify=True)
   if state['state'] in ('READY','QUEUED','RUNNING','BUSY'):return state
   if len(self.futures)>=self.max_pending:raise RuntimeError('Очередь Excel заполнена. Повторите позже.')
   folder=self.directory(key);folder.mkdir(exist_ok=True)
   queued={'format':VERSION,'generator':generator_version(),'key':key,'name':str(filename).replace('/',' ').replace('\\',' '),'state':'QUEUED','owner':self.boot,'pid':self.pid,'process_started':self.process_started,'heartbeat':time.time(),'created':time.time(),'attempt':state.get('attempt',0)}
   # Do not overwrite another process's active producer.
   try:
    with locked(self.root,key,timeout=.1):
     other=self._lease(key)
     if other and self._alive(key,other):return self.status(key)
     latest=self._read(key)
     if latest.get('state')=='READY' and self.status(key,verify=True).get('state')=='READY':return latest
     atomic_json(folder/'owner.json',queued)
     atomic_json(folder/'state.json',queued)
   except TimeoutError:return {'state':'BUSY'}
   start=threading.Event();self.starts[key]=start
   if not defer_start:start.set()
   future=self.executor.submit(self._wait_ui,key,builder,start);self.futures[key]=future
   future.add_done_callback(lambda f:self._finished(key,f));return queued
 def _finished(self,key,future):
  with self.guard:
   if self.futures.get(key) is future:self.futures.pop(key,None);self.starts.pop(key,None)
 def arm(self,key):
  with self.guard:
   if key in self.starts:self.starts[key].set()
 def _wait_ui(self,key,builder,start):
  # Permit the normal screen rerun to release its analytics gate first.
  # A disconnected browser cannot strand a queued job indefinitely.
  start.wait(30);self._run(key,builder)
 def cleanup(self,required=0,exclude=None):
  with locked(self.root,hashlib.sha256(b'EXCEL_CACHE_CLEANUP').hexdigest(),timeout=30):
   now=time.time()
   def usage():
    total=0
    for path in self.root.rglob('*'):
     try:
      if path.is_file() and not path.is_symlink():total+=path.stat().st_size
     except FileNotFoundError:pass
    return total
   from .temporary import reap
   reap(self.root)
   used=usage();candidates=[]
   for p in self.root.iterdir():
    if not p.is_dir() or len(p.name)!=64 or p.name==exclude:continue
    try:self.directory(p.name)
    except ValueError:continue
    r=self._read(p.name);candidates.append((r.get('finished',r.get('created',0)),p))
   for _,p in sorted(candidates,key=lambda v:v[0]):
    try:
     with locked(self.root,p.name,timeout=0):
      r=self._read(p.name);owner=self._lease(p.name)
      if owner and self._alive(p.name,owner):continue
      if r.get('state') in ('QUEUED','RUNNING') and self._alive(p.name,r):continue
      orphan=r.get('state') in ('QUEUED','RUNNING','MISSING','INVALID','ERROR')
      age=r.get('finished',r.get('created',0))
      if not orphan and (r.get('lease_until',0)>now or (used+required<=self.max_bytes and now-age<=self.ttl)):continue
      if p.resolve().parent!=self.root or p.is_symlink():raise ValueError('Unsafe cache cleanup')
      shutil.rmtree(p)
      with self.guard:
       old=self.hot.pop(p.name,None)
       if old:self.hot_bytes-=len(old[1])
    except (TimeoutError,FileNotFoundError):continue
    used=usage()
   if used+required>self.max_bytes:raise RuntimeError('Excel cache budget is occupied by protected active files')
   return used
 def _run(self,key,builder):
  folder=self.directory(key);temp=folder/('artifact.'+uuid.uuid4().hex+'.tmp')
  attempt=0
  try:
   with locked(self.root,key,timeout=7200):
    r=self._read(key);p=folder/'artifact.xlsx'
    if r['state']=='READY' and p.exists() and p.stat().st_size==r.get('bytes') and digest(p)==r.get('sha256'):return
    for stale in folder.glob('artifact.*.tmp'):
     if stale.resolve().parent!=folder or stale.is_symlink():raise ValueError('Invalid staging path')
     stale.unlink()
    with locked(self.root,hashlib.sha256(b'EXCEL_GLOBAL_PRODUCER').hexdigest(),timeout=7200):
     self.cleanup(required=self.reserve,exclude=key)
     with self.guard:
      attempt=r.get('attempt',0)+1
      r.update(format=VERSION,key=key,state='RUNNING',phase='BUILD',owner=self.boot,heartbeat=time.time(),started=time.time(),attempt=attempt);atomic_json(folder/'state.json',r)
     blob=builder()
     if not isinstance(blob,bytes) or len(blob)>self.reserve:raise ValueError('Workbook exceeds disk export budget')
     with temp.open('wb') as f:f.write(blob);f.flush();os.fsync(f.fileno())
     del blob
     with self.guard:
      r.update(phase='VERIFY',heartbeat=time.time());atomic_json(folder/'state.json',r)
     sheets=inspect_workbook(temp);checksum=digest(temp);size=temp.stat().st_size
     with self.guard:
      r.update(state='READY',phase='READY',finished=time.time(),heartbeat=time.time(),sha256=checksum,bytes=size,sheets=sheets,lease_until=time.time()+self.lease)
      encoded=json.dumps(r,ensure_ascii=False).encode()
      if len(encoded)>1024*1024:raise ValueError('Workbook manifest exceeds metadata budget')
      self.cleanup(required=2*len(encoded)+1024,exclude=key)
      os.replace(temp,p)
      atomic_json(folder/'state.json',r)
  except BaseException as error:
   from entry_points import diagnostics
   diagnostics.error(error,operation='public_excel.prepare')
   with self.guard:atomic_json(folder/'state.json',{'format':VERSION,'generator':generator_version(),'key':key,'state':'ERROR','owner':self.boot,'heartbeat':time.time(),'error_type':type(error).__name__,'attempt':attempt})
  finally:
   temp.unlink(missing_ok=True)
   try:
    with locked(self.root,key,timeout=.1):
     if self._lease(key).get('owner')==self.boot:(folder/'owner.json').unlink(missing_ok=True)
   except TimeoutError:pass
 def ready_bytes(self,key):
  with locked(self.root,key,timeout=5):
   r=self._read(key);p=self.directory(key)/'artifact.xlsx'
   if r['state']!='READY' or p.is_symlink() or not p.exists() or p.stat().st_size!=r.get('bytes'):raise RuntimeError('Кеш Excel повреждён. Подготовьте файл повторно.')
   stat=p.stat();signature=(r.get('sha256'),stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
   if r.get('lease_until',0)<time.time()+self.lease/2:r['lease_until']=time.time()+self.lease;atomic_json(self.directory(key)/'state.json',r)
   with self.guard:
    hit=self.hot.get(key)
    if hit and hit[0]==signature:self.hot.move_to_end(key);return hit[1]
   blob=p.read_bytes()
   if hashlib.sha256(blob).hexdigest()!=r.get('sha256'):raise RuntimeError('Кеш Excel повреждён. Подготовьте файл повторно.')
   with self.guard:
    old=self.hot.pop(key,None)
    if old:self.hot_bytes-=len(old[1])
    if len(blob)<=self.hot_limit:
     while self.hot and self.hot_bytes+len(blob)>self.hot_limit:
      _,old=self.hot.popitem(last=False);self.hot_bytes-=len(old[1])
     self.hot[key]=(signature,blob);self.hot_bytes+=len(blob)
   return blob
 def close(self):
  with self.guard:
   for start in self.starts.values():start.set()
  self.executor.shutdown(wait=True);self.closed.set();self.heartbeat.join(timeout=5)

_managers={};_guard=threading.Lock()
def manager():
 root=Path(os.environ.get('PUBLIC_XLSX_CACHE',str(Path(os.environ.get('PUBLIC_SEARCH_CACHE',tempfile.gettempdir()))/'xlsx_jobs_r12'))).resolve()
 with _guard:
  if root not in _managers:_managers[root]=Jobs(root)
  return _managers[root]
