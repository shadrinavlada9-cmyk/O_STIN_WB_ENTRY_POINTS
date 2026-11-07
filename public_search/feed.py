"""Validated, immutable Search generations; failed repair never moves old data."""
from pathlib import Path
import json,os,uuid,zipfile,urllib.request,tempfile,re,shutil
from entry_points.dataset import sha,safe_remote
from entry_points.locking import locked
MANIFEST_SCHEMA='PUBLIC_SEARCH_ARTIFACT_V1'
def allowed(name,kind):
 if kind=='OSTIN':return name=='data/current.json' or re.fullmatch(r'data/[a-f0-9]{16}/(?:sq|ep|dim|ep_flags)\.parquet',name) or re.fullmatch(r'data/[a-f0-9]{16}/inventory\.json',name)
 if kind=='MARKET':return name=='market_data/index.json' or re.fullmatch(r'market_data/snapshots/[a-f0-9]{64}/(?:meta\.json|rows\.parquet)',name)
 return False
def file_map(value,kind):
 return isinstance(value,dict) and bool(value) and all(isinstance(n,str) and allowed(n,kind) and isinstance(h,str) and re.fullmatch('[a-f0-9]{64}',h) for n,h in value.items())
class SearchFeed:
 def __init__(self,root=None,url=None,cache=None):
  self.root=Path(root) if root else None;self.url=safe_remote(url) if url else None;self.cache=Path(cache or Path(tempfile.gettempdir())/'public_search_r2');self.cache.mkdir(parents=True,exist_ok=True)
  try:
   if self.root:self.index=json.loads((self.root/'search_index.json').read_text(encoding='utf8'))
   elif self.url:
    with urllib.request.urlopen(self.url,timeout=30) as response:blob=response.read(1024*1024+1)
    if len(blob)>1024*1024:raise ValueError('Oversized Search index')
    self.index=json.loads(blob)
    if not isinstance(self.index,dict) or self.index.get('status')!='PUBLIC_SCOPE_APPROVED':raise ValueError('Search feed is not approved for public use')
   else:raise ValueError('Search feed not configured')
  except (OSError,UnicodeError,ValueError) as error:
   raise RuntimeError('Источник Search недоступен или повреждён. Повторите позже; ранее принятые файлы не изменены.') from error
  if not isinstance(self.index,dict) or self.index.get('schema')!='PUBLIC_SEARCH_INDEX_V1' or not isinstance(self.index.get('components'),list):raise ValueError('Unsupported Search feed')
 def ensure(self,kind):
  recs=[r for r in self.index['components'] if isinstance(r,dict) and r.get('kind')==kind]
  if len(recs)!=1:raise ValueError('Missing or ambiguous Search component')
  rec=recs[0];digest=rec.get('sha256','');filename=rec.get('file','')
  if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest) or not isinstance(filename,str) or not filename.startswith(kind+'_') or Path(filename).name!=filename or not file_map(rec.get('files'),kind):raise ValueError('Invalid artifact identity or manifest contract')
  def validate(folder):
   try:
    path=folder/'PUBLIC_MANIFEST.json'
    if not path.is_file() or path.stat().st_size>1024*1024:return False
    manifest=json.loads(path.read_text(encoding='utf8'))
    if not isinstance(manifest,dict) or set(manifest)!={'schema','kind','files'} or manifest['schema']!=MANIFEST_SCHEMA or manifest['kind']!=kind or not file_map(manifest['files'],kind) or manifest['files']!=rec['files']:return False
    return all((folder/n).is_file() and not (folder/n).is_symlink() and sha(folder/n)==h for n,h in rec['files'].items())
   except (OSError,ValueError,TypeError,UnicodeError):return False
  base=kind+'_'+digest;pointer=self.cache/(base+'.current.json')
  with locked(self.cache,digest):
   directory=base
   if pointer.exists():
    try:
     directory=json.loads(pointer.read_text(encoding='utf8'))['directory']
     if not isinstance(directory,str) or not re.fullmatch(re.escape(base)+r'(?:_[a-f0-9]{32})?',directory):directory=base
    except (OSError,ValueError,TypeError,KeyError,UnicodeError):directory=base
   target=self.cache/directory
   if target.exists() and validate(target):return target
   if target.exists():
    # Existing sessions may hold files open. Mark this directory unusable for
    # new requests but never rename, overwrite or delete the prior generation.
    (self.cache/(base+'.invalid.json')).write_text(json.dumps({'directory':directory,'category':'CACHE_INTEGRITY_FAILED'}),encoding='utf8')
   archive=self.root/filename if self.root else self.cache/(digest+'.zip')
   for attempt in range(3):
    stage=self.cache/(base+'_'+uuid.uuid4().hex+'.staging');download=None;marker=None
    try:
     if not self.root and (not archive.exists() or sha(archive)!=digest):
      download=archive.with_name(archive.name+'.'+uuid.uuid4().hex+'.tmp')
      with urllib.request.urlopen(safe_remote(rec['url']),timeout=90) as source,download.open('wb') as out:
       total=0
       while chunk:=source.read(1024*1024):
        total+=len(chunk)
        if total>256*1024**2:raise ValueError('Search archive size budget exceeded')
        out.write(chunk)
      if sha(download)!=digest:raise ValueError('Search ZIP SHA mismatch')
      os.replace(download,archive)
     if sha(archive)!=digest:raise ValueError('Search artifact SHA mismatch')
     stage.mkdir()
     with zipfile.ZipFile(archive) as z:
      names=z.namelist()
      if len(names)!=len(set(names)) or set(names)!=set(rec['files'])|{'PUBLIC_MANIFEST.json'}:raise ValueError('Unexpected Search archive contents')
      if sum(i.file_size for i in z.infolist())>1024**3 or z.getinfo('PUBLIC_MANIFEST.json').file_size>1024*1024:raise ValueError('Unpacked Search budget exceeded')
      for n in names:
       if n!='PUBLIC_MANIFEST.json' and not allowed(n,kind):raise ValueError('Non-Search file rejected')
       dest=stage/n;dest.parent.mkdir(parents=True,exist_ok=True)
       with z.open(n) as source,dest.open('wb') as out:shutil.copyfileobj(source,out,1024*1024)
     if not validate(stage):raise ValueError('Restored Search integrity mismatch')
     published=self.cache/(base+'_'+uuid.uuid4().hex);os.replace(stage,published)
     marker=pointer.with_name(pointer.name+'.'+uuid.uuid4().hex+'.tmp');marker.write_text(json.dumps({'directory':published.name}),encoding='utf8');os.replace(marker,pointer)
     return published
    except (OSError,ValueError,TypeError,KeyError,UnicodeError,zipfile.BadZipFile) as error:
     if attempt==2:raise RuntimeError('Не удалось восстановить кеш Search после трёх попыток. Проверьте доступность источника и повторите; предыдущие поколения сохранены.') from error
    finally:
     if download is not None:download.unlink(missing_ok=True)
     if marker is not None:marker.unlink(missing_ok=True)
     if stage.exists():shutil.rmtree(stage)
