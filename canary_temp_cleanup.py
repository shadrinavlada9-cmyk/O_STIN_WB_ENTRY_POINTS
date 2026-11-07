"""Canary-only spool lifecycle adapter; no workbook/job identity changes.

Only PID/start-time/UUID-owned children of the configured temporary root are
eligible. Live/uncertain owners are always protected. Ready cache is untouched.
"""
from pathlib import Path
import threading,time,os,re,shutil,logging,hashlib
import psutil
LOG=logging.getLogger('canary.xlsx_temporary')
_PATTERN=re.compile(r'^(\d+)_(\d+)_([0-9a-f]{32})$')
_COORD=hashlib.sha256(b'EXCEL_CACHE_CLEANUP').hexdigest()
_SERVICE=None;_GUARD=threading.RLock()

class Lifecycle:
 def __init__(self,base,*,max_directories=256,max_bytes=512*1024**2,interval=5):
  raw=Path(base)
  if raw.is_symlink():raise ValueError('Invalid Canary XLSX cache root')
  self.base=raw.resolve();self.base.mkdir(parents=True,exist_ok=True)
  self.parent=self.base/'temporary';self.max_directories=max_directories;self.max_bytes=max_bytes;self.interval=interval
  self.guard=threading.RLock();self.stop_event=threading.Event();self.thread=None
  self.original=None;self.wrapper=None;self.last={};self.removed_total=0;self.reclaimed_total=0;self.sweeps=0;self.quota_rejections=0
 def _parent(self):
  if self.parent.is_symlink() or self.parent.resolve().parent!=self.base:raise ValueError('Invalid Canary XLSX temporary root')
  self.parent.mkdir(exist_ok=True);return self.parent
 def _active(self,pid,started_ms):
  try:
   p=psutil.Process(pid)
   return abs(p.create_time()*1000-started_ms)<10 and p.is_running() and p.status()!=psutil.STATUS_ZOMBIE
  except (psutil.NoSuchProcess,psutil.ZombieProcess):return False
  except (psutil.AccessDenied,OSError):return True # inability to prove death protects files
 def _bytes(self,p):
  total=0
  for parent,dirs,files in os.walk(p,followlinks=False):
   dirs[:]=[n for n in dirs if not (Path(parent)/n).is_symlink()]
   for name in files:
    f=Path(parent)/name
    try:
     if not f.is_symlink():total+=f.stat().st_size
    except FileNotFoundError:pass
  return total
 def _scan(self):
  rows=[];unknown=0;owners={}
  for p in self._parent().iterdir():
   m=_PATTERN.fullmatch(p.name)
   if not m or p.is_symlink() or not p.is_dir():unknown+=1;continue
   owner=(int(m[1]),int(m[2]))
   if owner not in owners:owners[owner]=self._active(*owner)
   rows.append((p,owners[owner],self._bytes(p)))
  return rows,unknown
 def _recover_locked(self):
  rows,unknown=self._scan();before_bytes=sum(r[2] for r in rows);removed=0;reclaimed=0;blocked=0
  for p,active,size in rows:
   if active:continue
   # Recheck owner just before deletion; no TTL is a deletion criterion.
   m=_PATTERN.fullmatch(p.name)
   if p.is_symlink() or p.resolve().parent!=self.parent.resolve() or self._active(int(m[1]),int(m[2])):continue
   try:shutil.rmtree(p);removed+=1;reclaimed+=size
   except FileNotFoundError:pass # another safe finalizer/reaper already won
   except OSError:blocked+=1 # Windows open handles/permissions: retry next sweep
  after,unknown_after=self._scan() if removed or blocked else (rows,unknown)
  self.removed_total+=removed;self.reclaimed_total+=reclaimed;self.sweeps+=1
  self.last={'owned_directories_before':len(rows),'owned_bytes_before':before_bytes,'owned_directories_after':len(after),'owned_bytes_after':sum(r[2] for r in after),'active_protected':sum(r[1] for r in after),'orphan_remaining':sum(not r[1] for r in after),'unknown_entries_skipped':unknown_after,'deletion_retry_count':blocked,'removed':removed,'reclaimed_bytes':reclaimed}
  if removed or blocked:LOG.warning('CANARY_XLSX_TEMP_CLEANUP %s',self.last)
  return dict(self.last)
 def recover(self):
  from public_search.excel_job_locks import locked
  with self.guard,locked(self.base,_COORD,timeout=30):return self._recover_locked()
 def install(self):
  from public_search import temporary
  if temporary.root()!=self.base:raise ValueError('Canary cleanup root mismatch')
  if self.original is not None:return
  self.recover();self.original=temporary.make_directory
  def allocate():
   from public_search.excel_job_locks import locked
   if temporary.root()!=self.base:raise ValueError('Canary XLSX root changed')
   with self.guard,locked(self.base,_COORD,timeout=30):
    state=self._recover_locked()
    if state['owned_directories_after']>=self.max_directories or state['owned_bytes_after']>=self.max_bytes:
     self.quota_rejections+=1
     raise RuntimeError('Временный кеш Excel занят активными запросами. Завершите выгрузки и повторите; готовые файлы сохранены.')
    return self.original()
  self.wrapper=allocate;temporary.make_directory=allocate
 def _run(self):
  while not self.stop_event.wait(self.interval):
   try:self.recover()
   except Exception as e:LOG.warning('CANARY_XLSX_TEMP_CLEANUP_RETRY %s',type(e).__name__)
 def begin(self):
  if self.thread is None:
   self.thread=threading.Thread(target=self._run,daemon=True,name='canary-xlsx-spool-reaper');self.thread.start()
 def snapshot(self):
  with self.guard:return {'scope':'ONLY_OWN_PID_START_UUID_TEMPORARY_CHILDREN','startup_and_periodic_recovery':True,'ready_artifacts_never_scanned_or_removed':True,'sweeps':self.sweeps,'removed_total':self.removed_total,'reclaimed_bytes_total':self.reclaimed_total,'quota_rejections':self.quota_rejections,'admission_max_directories':self.max_directories,'admission_max_bytes':self.max_bytes,'admission_is_not_preemption_of_active_writes':True,'last':dict(self.last)}
 def close(self):
  self.stop_event.set()
  if self.thread:self.thread.join(timeout=35)
  from public_search import temporary
  if self.wrapper is not None and temporary.make_directory is self.wrapper:temporary.make_directory=self.original

def start(base):
 global _SERVICE
 with _GUARD:
  if _SERVICE is None:
   service=Lifecycle(base);service.install();service.begin();_SERVICE=service
  elif _SERVICE.base!=Path(base).resolve():raise ValueError('Canary XLSX cleanup root changed')
  return _SERVICE
def snapshot():return _SERVICE.snapshot() if _SERVICE is not None else {'started':False}
