"""Canary-only bounded telemetry. Never reads environment secrets or WB data."""
from pathlib import Path
from collections import deque
import os,time,json,threading,uuid,shutil,logging
import psutil
import canary_sqlite_diagnostics as sqlite_diag
import canary_temp_cleanup
LOG=logging.getLogger('canary.resources')
def values(path):
 try:return {k:int(v) for k,v in (line.split() for line in path.read_text().splitlines())}
 except (OSError,ValueError):return None
def scalar(path):
 try:
  value=path.read_text().strip();return int(value) if value.isdigit() else value
 except OSError:return None
def cgroup():
 try:records=Path('/proc/self/cgroup').read_text().splitlines()
 except OSError:return {'available':False,'reason':'NO_PROC_CGROUP'}
 for record in records:
  if record.startswith('0::'):
   relative=record.split('::',1)[1].lstrip('/');base=Path('/sys/fs/cgroup');specific=base/relative
   directory=specific if (specific/'memory.current').exists() else base
   if not (directory/'memory.current').exists():break
   stat=values(directory/'memory.stat') or {}
   return {'available':True,'version':2,'scope':'self cgroup path' if directory==specific else 'namespace mount root; requires scope verification','memory_current':scalar(directory/'memory.current'),'memory_peak':scalar(directory/'memory.peak'),'memory_max':scalar(directory/'memory.max'),'memory_high':scalar(directory/'memory.high'),'swap_max':scalar(directory/'memory.swap.max'),'events':values(directory/'memory.events'),'anon':stat.get('anon'),'file':stat.get('file'),'kernel':stat.get('kernel'),'shmem':stat.get('shmem'),'sample_interval_seconds':2}
 for record in records:
  parts=record.split(':',2)
  if len(parts)==3 and 'memory' in parts[1].split(','):
   directory=Path('/sys/fs/cgroup/memory')/parts[2].lstrip('/')
   if not (directory/'memory.usage_in_bytes').exists():directory=Path('/sys/fs/cgroup/memory')
   return {'available':(directory/'memory.usage_in_bytes').exists(),'version':1,'memory_current':scalar(directory/'memory.usage_in_bytes'),'memory_peak':scalar(directory/'memory.max_usage_in_bytes'),'memory_max':scalar(directory/'memory.limit_in_bytes'),'stat':values(directory/'memory.stat'),'failcnt':scalar(directory/'memory.failcnt'),'scope':'current memory controller; requires scope verification'}
 return {'available':False,'reason':'MEMORY_CONTROLLER_NOT_READABLE'}
class Monitor:
 def __init__(self,root):
  self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.boot=uuid.uuid4().hex;self.started=time.time();self.guard=threading.RLock();self.samples=deque(maxlen=1200);self.cycle=1
  thread=threading.Thread(target=self.run,daemon=True,name='canary-telemetry');thread.start()
 def sample(self):
  p=psutil.Process();disk=shutil.disk_usage(self.root);total=0;temporary=0;locks=0;states={}
  for item in self.root.rglob('*'):
   try:
    if item.is_file() and not item.is_symlink():
     total+=item.stat().st_size
     if item.suffix=='.tmp' or 'temporary' in item.parts:temporary+=1
     if item.suffix=='.lock':locks+=1
     if item.name=='state.json':
      try:
       state=json.loads(item.read_text(encoding='utf8')).get('state','INVALID');states[state]=states.get(state,0)+1
      except (OSError,ValueError):states['INVALID']=states.get('INVALID',0)+1
   except FileNotFoundError:pass
  return {'at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'boot':self.boot,'identity':getattr(self,'identity',None),'uptime_seconds':round(time.time()-self.started,3),'cycle':self.cycle,'sqlite_diagnostics':sqlite_diag.summary(),'xlsx_temp_cleanup':canary_temp_cleanup.snapshot(),'worker_RSS':p.memory_info().rss,'cgroup':cgroup(),'runtime_disk_bytes':total,'temporary_files':temporary,'fixed_lock_files':locks,'jobs':states,'filesystem_available_bytes':disk.free,'filesystem_total_bytes':disk.total,'filesystem_numbers_are_NOT_app_disk_quota':True}
 def run(self):
  while True:
   try:
    sample=self.sample()
    with self.guard:self.samples.append(sample)
    LOG.warning('CANARY_RESOURCE %s',json.dumps(sample,sort_keys=True))
   except Exception as error:LOG.warning('CANARY_TELEMETRY_UNAVAILABLE %s',type(error).__name__)
   time.sleep(2)
 def snapshot(self):
  with self.guard:return {'data':'SYNTHETIC_ONLY','boot':self.boot,'identity':getattr(self,'identity',None),'sqlite_diagnostics':sqlite_diag.summary(),'xlsx_temp_cleanup':canary_temp_cleanup.snapshot(),'samples':list(self.samples),'actual_cloud_result':'ONLY_IF_HOSTED_ON_THE_TARGET_PLATFORM','scope_warning':'RSS, filesystem free space and unreadable cgroup values are never substitutes for measured container limits'}
