"""Unchanged accepted cross-platform per-SHA publication lock."""
from contextlib import contextmanager
import threading,os,time
_threads_guard=threading.Lock();_threads={}
@contextmanager
def locked(base,sha,timeout=180):
 with _threads_guard:lock=_threads.setdefault((str(base),sha),threading.RLock())
 if not lock.acquire(timeout=timeout):raise TimeoutError('Snapshot thread lock timeout')
 try:
  directory=base/'wb_cloud_locks';directory.mkdir(exist_ok=True)
  with (directory/(sha+'.lock')).open('a+b') as f:
   f.seek(0,2)
   if f.tell()==0:f.write(b'0');f.flush()
   limit=time.monotonic()+timeout
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
     if time.monotonic()>=limit:raise TimeoutError('Snapshot process lock timeout') from None
     time.sleep(.05)
   try:yield
   finally:
    f.seek(0)
    if os.name=='nt':
     import msvcrt
     msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
    else:
     import fcntl
     fcntl.flock(f.fileno(),fcntl.LOCK_UN)
 finally:lock.release()
