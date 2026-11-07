"""Crash-reclaimable export temporary directories, owned by PID + start time.

Never remove another live worker's files. No guessed TTL for live processes.
The ownership identity is in the directory name, so a torn JSON cannot turn a
live spool into a cleanup candidate. Only derivative export files live here.
"""
from pathlib import Path
import os,time,uuid,re,contextlib,shutil,tempfile
import psutil
_pattern=re.compile(r'^(\d+)_(\d+)_([0-9a-f]{32})$')
def root():
 return Path(os.environ.get('PUBLIC_XLSX_CACHE',str(Path(os.environ.get('PUBLIC_SEARCH_CACHE',tempfile.gettempdir()))/'xlsx_jobs_r12'))).resolve()
def alive(pid,started_ms):
 try:
  p=psutil.Process(pid)
  return abs(p.create_time()*1000-started_ms)<10 and p.is_running() and p.status()!=psutil.STATUS_ZOMBIE
 except (psutil.NoSuchProcess,psutil.ZombieProcess):return False
 except psutil.AccessDenied:return True
def make_directory():
 base=root()/'temporary';base.mkdir(parents=True,exist_ok=True)
 if base.is_symlink() or base.resolve().parent!=root():raise ValueError('Invalid temporary export root')
 path=base/(str(os.getpid())+'_'+str(int(psutil.Process().create_time()*1000))+'_'+uuid.uuid4().hex)
 path.mkdir();return path
@contextlib.contextmanager
def directory():
 path=make_directory()
 try:yield str(path)
 finally:shutil.rmtree(path,ignore_errors=True)
def reap(base):
 """Called under cache cleanup coordinator; creation identity is atomic mkdir."""
 parent=Path(base).resolve()/'temporary';removed=0
 if not parent.exists():return 0
 if parent.is_symlink() or parent.resolve().parent!=Path(base).resolve():raise ValueError('Invalid temporary export root')
 for p in parent.iterdir():
  match=_pattern.fullmatch(p.name)
  if not match or p.is_symlink() or not p.is_dir():continue
  if alive(int(match[1]),int(match[2])):continue
  shutil.rmtree(p);removed+=1
 return removed
