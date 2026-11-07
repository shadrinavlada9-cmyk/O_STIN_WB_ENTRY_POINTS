from public_search.temporary import make_directory
"""Ephemeral, request-owned export spools. Streamlit retains descriptors, never large frames."""
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Mapping
import shutil,tempfile,uuid,weakref,sys
import pyarrow.parquet as pq
from .selected_data import GATE
@dataclass(frozen=True)
class Spool:
 path:Path
 columns:tuple
 count:int
 def __len__(self):return self.count
 def __iter__(self):return iter(self.columns)
 def itertuples(self,index=False,name=None):
  for batch in pq.ParquetFile(self.path).iter_batches(batch_size=1024):
   yield from batch.to_pandas().itertuples(index=False,name=None)
class Chunked:
 """One logical worksheet over bounded source chunks, with unioned columns."""
 def __init__(self,folder,chunks):
  self.folder=folder;self.chunks=tuple(chunks)
  self.columns=tuple(dict.fromkeys(c for chunk in chunks for c in chunk.columns))
  self.count=sum(len(chunk) for chunk in chunks)
  self._cleanup=weakref.finalize(self,shutil.rmtree,folder,True)
 def __len__(self):return self.count
 def __iter__(self):return iter(self.columns)
 def itertuples(self,index=False,name=None):
  for chunk in self.chunks:
   for batch in pq.ParquetFile(chunk.path).iter_batches(batch_size=1024):
    yield from batch.to_pandas().reindex(columns=self.columns).itertuples(index=False,name=None)
class Ticket(Mapping):
 def __init__(self,folder,items,sha=None):
  self.folder=folder;self._items=items;self.sha=sha
  self._cleanup=weakref.finalize(self,shutil.rmtree,folder,True)
 def __len__(self):return len(self._items)
 def __iter__(self):return iter(self._items)
 def __getitem__(self,key):return self._items[key]
 def to_excel(self):
  from .r32_tables import excel
  with GATE:
   try:return excel(self)
   except Exception as error:
    import cloud_diagnostics as diagnostics
    diagnostics.error(error,operation='search.excel',sha=self.sha)
    raise RuntimeError('Не удалось сформировать Excel. Обновите срез; подробности сохранены в серверном журнале.') from None
class Sheets(Ticket):
 """Request-owned streaming spool: never retain all presentation frames."""
 def __init__(self):
  folder=make_directory()
  runtime=sys.modules.get('cloud_runtime');sha=None
  if runtime is not None:
   try:sha=runtime.context()['sha256']
   except RuntimeError:pass
  super().__init__(folder,{},sha);self.size=0;self.sequence=0
 def __setitem__(self,key,frame):
  if callable(frame):self._items[key]=frame;return
  path=self.folder/(str(self.sequence)+'.parquet');self.sequence+=1
  frame.to_parquet(path,index=False);self.size+=path.stat().st_size
  if self.size>256*1024**2:raise ValueError('Excel export spool exceeds its explicit disk budget')
  self._items[key]=Spool(path,tuple(frame.columns),len(frame))

 def dataframe(self,key):
  # Intended only for tiny metadata, never full result tables.
  return pq.read_table(self[key].path).to_pandas()

def prepare(sheets):
 if isinstance(sheets,Ticket):return sheets
 folder=make_directory()
 refs={};size=0
 try:
  for i,(name,f) in enumerate(sheets.items()):
   if callable(f):refs[name]=f;continue
   path=folder/(str(i)+'.parquet');f.to_parquet(path,index=False)
   size+=path.stat().st_size
   if size>256*1024**2:raise ValueError('Excel export spool exceeds its explicit disk budget')
   refs[name]=Spool(path,tuple(f.columns),len(f))
  runtime=sys.modules.get('cloud_runtime');sha=None
  if runtime is not None:
   try:sha=runtime.context()['sha256']
   except RuntimeError:pass
  return Ticket(folder,refs,sha)
 except Exception:shutil.rmtree(folder);raise
