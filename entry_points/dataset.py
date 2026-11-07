"""Read only the isolated, allowlisted Entry Points feed. No WB credentials."""
from pathlib import Path
import hashlib,json,sqlite3,zipfile,tempfile,threading,os,uuid,urllib.request,urllib.parse,re,datetime as dt
from .locking import locked
from .classifier import VERSION as CLASSIFIER_VERSION
SCHEMA='OSTIN_ENTRY_POINTS_PUBLIC_REVIEW_V2'
TABLES={
 'products':['id','nm_id','seller_article','cm','gender','collection','product_type'],
 'entries':['product_id','section','entry_point','clicks','carts','orders','impressions','row_anomaly','ordinal'],
 'queries':['id','query_norm','qclass','qrule'],
 'query_filters':['product_id','query_id'],
 'flags':['section','entry_point','anomaly'],
 'anomalies':['product_id','section','entry_point','clicks','carts','orders','impressions','source_ctr','source_cart_cr','source_order_cr','reason','ordinal'],
 'metadata':['key','value']}
LOCK=threading.RLock()
def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  while block:=f.read(1024*1024):h.update(block)
 return h.hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf8'))
def connect(path):
 con=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
 con.execute('PRAGMA query_only=ON');con.execute('PRAGMA trusted_schema=OFF');con.execute('PRAGMA cache_size=-32768');con.execute('PRAGMA mmap_size=0');return con
def validate_db(path,record,deep=True):
 if sha(path)!=record['database_sha256']:raise ValueError('Нарушена целостность подготовленных данных')
 con=connect(path)
 try:
  objects=con.execute("SELECT name,type FROM sqlite_master WHERE type IN ('table','view','trigger')").fetchall()
  if dict(objects)!={name:'table' for name in TABLES}:raise ValueError('В наборе есть посторонние таблицы')
  for name,columns in TABLES.items():
   if [r[1] for r in con.execute('PRAGMA table_info('+name+')')]!=columns:raise ValueError('Непредусмотренный состав полей '+name)
  if deep:
   # Check the bounded prepared DB in RAM once on publication. This avoids
   # pathological random I/O on Windows/WSL mounts; warm reads still hash the
   # actual file and verify its schema/period, never trust a COMPLETE marker.
   probe=sqlite3.connect(':memory:')
   try:
    probe.deserialize(Path(path).read_bytes());probe.execute('PRAGMA query_only=ON');probe.execute('PRAGMA trusted_schema=OFF')
    if probe.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Повреждён аналитический набор')
   finally:probe.close()
  meta={k:json.loads(v) for k,v in con.execute('SELECT key,value FROM metadata')}
  if set(meta)!={'schema','week','start','end','classifier','report','query_index_purpose','source_rows'}:raise ValueError('Посторонние поля метаданных')
  if meta['schema']!=SCHEMA or meta['classifier']!=CLASSIFIER_VERSION or meta['week']!=record['week'] or any(meta[k]!=record[k] for k in ['start','end']):raise ValueError('Не совпадает период/формат/классификатор набора')
 finally:con.close()
def safe_remote(url):
 u=urllib.parse.urlparse(url)
 if u.scheme!='https' or u.hostname not in ('drive.google.com','drive.usercontent.google.com'):raise ValueError('Непредусмотренный адрес публичного набора')
 return url
class Feed:
 def __init__(self,root=None,index_url=None,cache=None):
  self.root=Path(root).resolve() if root else None;self.url=safe_remote(index_url) if index_url else None
  self.cache=Path(cache or Path(tempfile.gettempdir())/'ostin_entry_points_only');self.cache.mkdir(parents=True,exist_ok=True)
  self.index_marker=self.cache/('index_'+hashlib.sha256(str(self.root or self.url).encode()).hexdigest()+'.json')
  if not self.root and not self.url:raise ValueError('Не указан подготовленный набор точек входа')
 def index(self):
  if self.root:d=read(self.root/'index.json')
  else:
   try:
    with urllib.request.urlopen(self.url,timeout=30) as response:blob=response.read(1024*1024+1)
    if len(blob)>1024*1024:raise ValueError('Слишком большой индекс источников')
    d=json.loads(blob.decode('utf8'))
   except (OSError,ValueError):
    if not self.index_marker.exists():raise
    d=read(self.index_marker);d['runtime_warning']='Источник обновления временно недоступен. Показаны последние проверенные периоды с их фактическими датами.'
  if d.get('schema')!=SCHEMA or not d.get('weeks'):raise ValueError('Нет принятого набора точек входа')
  if len({r['week'] for r in d['weeks']})!=len(d['weeks']):raise ValueError('Неоднозначные версии одной недели')
  for record in d['weeks']:
   name=record['file']
   if Path(name).name!=name or not name.startswith('ENTRY_') or not name.endswith('.zip'):raise ValueError('Неверное имя подготовленного файла')
   if not all(re.fullmatch('[a-f0-9]{64}',record.get(k,'')) for k in ['sha256','database_sha256']):raise ValueError('Не указан точный SHA256')
   if not re.fullmatch(r'\d{4}-W\d{2}',record['week']):raise ValueError('Неверная неделя')
   start=dt.date.fromisoformat(record['start']);end=dt.date.fromisoformat(record['end'])
   iso=start.isocalendar();calendar_week=f'{iso.year}-W{iso.week:02d}'
   if start.weekday()!=0 or end-start!=dt.timedelta(days=6) or calendar_week!=record['week']:raise ValueError('Не подтверждён календарный период')
  return d
 def remember(self,index):
  temp=self.index_marker.with_name(self.index_marker.name+'.'+uuid.uuid4().hex+'.tmp')
  clean={k:v for k,v in index.items() if k!='runtime_warning'};temp.write_text(json.dumps(clean,ensure_ascii=False),encoding='utf8');os.replace(temp,self.index_marker)
 def database(self,record):
  digest=record['database_sha256']
  if not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Неверный SHA256 базы')
  pointer=self.cache/(digest+'.generation.json')
  with locked(self.cache,digest):
   for abandoned in self.cache.glob(digest+'*.tmp'):
    if abandoned.resolve().parent==self.cache.resolve():abandoned.unlink()
   filename=read(pointer)['file'] if pointer.exists() else digest+'.sqlite3'
   if not re.fullmatch(re.escape(digest)+r'(?:_[a-f0-9]{32})?\.sqlite3',filename):raise ValueError('Неверный указатель кеша')
   target=self.cache/filename
   if target.exists():
    try:validate_db(target,record,deep=False);return target
    except (ValueError,sqlite3.DatabaseError):
     # An existing session may still hold this file open on Windows. Publish
     # a fresh immutable generation; never replace/delete its open database.
     (self.cache/(digest+'.invalid.json')).write_text(json.dumps({'file':filename}),encoding='utf8')
     target=self.cache/(digest+'_'+uuid.uuid4().hex+'.sqlite3')
   archive=self.root/record['file'] if self.root else self.cache/(record['sha256']+'.zip')
   if not self.root and (not archive.exists() or sha(archive)!=record['sha256']):
    # Bounded recovery under the per-generation lock. Never publish unverified
    # bytes or change the accepted database pointer during download failures.
    url=safe_remote(record['url'])
    for attempt in range(3):
     stage=archive.with_name(archive.name+'.'+uuid.uuid4().hex+'.tmp')
     try:
      with urllib.request.urlopen(url,timeout=90) as response,stage.open('wb') as out:
       total=0
       while block:=response.read(1024*1024):
        total+=len(block)
        if total>64*1024**2:raise ValueError('Превышен размер недельного пакета')
        out.write(block)
      if sha(stage)!=record['sha256']:raise ValueError('Неверный SHA256 недельного пакета')
      os.replace(stage,archive)
      break
     except (OSError,ValueError) as error:
      if attempt==2:
       raise RuntimeError('Не удалось восстановить недельный архив после трёх попыток. Проверьте доступность источника и повторите запуск; принятые данные сохранены.') from error
     finally:
      if stage.exists():stage.unlink()
   if sha(archive)!=record['sha256']:raise ValueError('Неверный SHA256 недельного пакета')
   stage=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
   try:
    with zipfile.ZipFile(archive) as z:
     if set(z.namelist())!={'entry_points.sqlite3','manifest.json'}:raise ValueError('В пакет включены посторонние файлы')
     if z.getinfo('entry_points.sqlite3').file_size>512*1024**2 or z.getinfo('manifest.json').file_size>1024*1024:raise ValueError('Превышен размер содержимого')
     meta=json.loads(z.read('manifest.json').decode('utf8'))
     if meta['schema']!=SCHEMA or meta['week']!=record['week'] or meta['database_sha256']!=record['database_sha256']:raise ValueError('Неверный manifest')
     with z.open('entry_points.sqlite3') as source,stage.open('wb') as out:
      while block:=source.read(1024*1024):out.write(block)
    validate_db(stage,record);os.replace(stage,target)
    marker=pointer.with_name(pointer.name+'.'+uuid.uuid4().hex+'.tmp');marker.write_text(json.dumps({'file':target.name}),encoding='utf8');os.replace(marker,pointer)
   finally:
    if stage.exists():stage.unlink()
  return target
