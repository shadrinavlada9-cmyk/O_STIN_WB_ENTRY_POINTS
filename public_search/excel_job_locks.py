"""Bounded, inode-stable job locks. Feed locking remains unchanged.

64 stripes + two coordinators per cache, never one file per job. Open handles
and weak thread pools retire after the last holder/waiter. Fixed files must NOT
be unlinked while workers run: deleting locked inodes would split exclusion.
"""
from pathlib import Path
import threading,weakref,contextlib,os,time,hashlib
STRIPES=64
_guard=threading.Lock();_pools=weakref.WeakValueDictionary();_local=threading.local()
class Pool:
 def __init__(self):self.locks=[threading.RLock() for _ in range(STRIPES+2)]
def slot(key):
 if key==hashlib.sha256(b'EXCEL_GLOBAL_PRODUCER').hexdigest():return STRIPES
 if key==hashlib.sha256(b'EXCEL_CACHE_CLEANUP').hexdigest():return STRIPES+1
 return int(key,16)%STRIPES
@contextlib.contextmanager
def locked(base,key,timeout=180):
 base=Path(base).resolve();base.mkdir(parents=True,exist_ok=True);number=slot(key);poolkey=str(base)
 with _guard:
  pool=_pools.get(poolkey)
  if pool is None:pool=Pool();_pools[poolkey]=pool
 lock=pool.locks[number]
 if not lock.acquire(timeout=timeout):raise TimeoutError('Excel thread lock busy')
 handles=getattr(_local,'handles',None)
 if handles is None:handles={};_local.handles=handles
 identity=(poolkey,number)
 try:
  if identity in handles:
   # Reentrant cleanup on a colliding stripe reuses the held OS handle.
   yield;return
  directory=base/'job_coordination';directory.mkdir(exist_ok=True)
  if directory.is_symlink() or directory.resolve().parent!=base:raise ValueError('Invalid coordination directory')
  path=directory/('slot_'+str(number)+'.lock')
  if path.is_symlink():raise ValueError('Invalid coordination file')
  with path.open('a+b') as f:
   if f.seek(0,2)==0:f.write(b'0');f.flush()
   deadline=time.monotonic()+timeout
   while True:
    try:
     f.seek(0)
     if os.name=='nt':
      import msvcrt
      msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
     else:
      import fcntl
      fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
     break
    except (OSError,BlockingIOError):
     if time.monotonic()>=deadline:raise TimeoutError('Excel process lock busy') from None
     time.sleep(.02)
   handles[identity]=f
   try:yield
   finally:
    handles.pop(identity,None);f.seek(0)
    if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
    else:fcntl.flock(f.fileno(),fcntl.LOCK_UN)
 finally:lock.release()
