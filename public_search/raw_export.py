"""Bounded full-source CSV/ZIP export. No row cap or Excel row-limit truncation."""
from pathlib import Path
from public_search.temporary import directory as export_directory
from dataclasses import dataclass
import json,tempfile,zipfile,io,csv
def safe(value):
 if value is None:return ''
 import pandas as pd
 if pd.isna(value):return ''
 if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@','\t','\r')):return "'"+value
 return value
def write_frames(z,name,frames):
 count=0
 with z.open(name,'w') as raw:
  with io.TextIOWrapper(raw,encoding='utf-8-sig',newline='') as text:
   writer=csv.writer(text);header=None
   for frame in frames:
    if header is None:header=list(frame.columns);writer.writerow(header)
    else:frame=frame.reindex(columns=header)
    for row in frame.itertuples(index=False,name=None):writer.writerow([safe(v) for v in row]);count+=1
 return count
def zip_frames(sources,metadata=None):
 with export_directory() as tmp:
  path=Path(tmp)/'data.zip';counts={}
  with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED,allowZip64=True) as z:
   for name,frames in sources.items():counts[name]=write_frames(z,name,frames)
   z.writestr('EXPORT_INFO.json',json.dumps({'rows':counts,'CSV_formula_like_text':'Leading apostrophe added only in CSV text for spreadsheet safety','no_TOP_or_page_truncation':True,'selection':metadata or {}},ensure_ascii=False))
  if path.stat().st_size>512*1024**2:raise ValueError('Export ZIP exceeds explicit 512 MiB output budget')
  return path.read_bytes()
@dataclass(frozen=True)
class EntryRaw:
 root:str|None;url:str|None;cache:str;index_json:str;a:str;b:str;filters_json:str
 def build(self):
  from entry_points.dataset import Feed
  from entry_points.analysis import Session,where
  import pandas as pd
  def frames():
   with Session(Feed(self.root,self.url,self.cache),json.loads(self.index_json),self.a,self.b,json.loads(self.filters_json)) as session:
    predicate,args=where(session.filters)
    if session.scope is not None:predicate+=' AND p.nm_id IN(SELECT value FROM json_each(?))';args.append(session.scope)
    if session.filters.get('entry'):predicate+=" AND (e.section || ' · ' || e.entry_point) IN(SELECT value FROM json_each(?))";args.append(json.dumps(session.filters['entry']))
    columns=['nm_id','seller_article','cm','gender','collection','product_type','section','entry_point','clicks','carts','orders','impressions','row_anomaly']
    for week,con in session.connections.items():
     query='SELECT '+','.join(('p.' if c in columns[:6] else 'e.')+c for c in columns)+' FROM entries e JOIN products p ON p.id=e.product_id WHERE 1'+predicate+' ORDER BY e.ordinal'
     for f in pd.read_sql_query(query,con,params=args,chunksize=4096):f.insert(0,'week',week);yield f
  return zip_frames({'Portrait_all_filtered_rows.csv':frames()},{'weeks':[self.a,self.b],'filters':json.loads(self.filters_json)})
@dataclass(frozen=True)
class OstinRaw:
 root:str;a:str;b:str;filters_json:str;sources:tuple=('sq',);model_scope:bool=False
 def build(self):
  from wb_search_r32 import analytics as A
  with A.SD.GATE:
   previous=A.ROOT;A.ROOT=Path(self.root)
   try:return self._build()
   finally:A.ROOT=previous
 def _build(self):
  from wb_search_r32 import analytics as A,r31_views as V
  import pyarrow as pa,pyarrow.dataset as ds
  filters=json.loads(self.filters_json);folder=Path(self.root)/'data'/A.pointer()['revision'];entry_ids=None;query_ids=None
  # Exact same product membership rule as the accepted entry filter, before
  # processing SQ batches; never keep the full two-week EP frame in memory.
  if filters.get('entry'):
   entry_ids=set()
   for batch in ds.dataset(folder/'ep.parquet').scanner(filter=ds.field('week').isin([self.a,self.b]),batch_size=16384).to_batches():
    select=V.cohort_filters(filters)|{'_sources':('ep',),'_export_source':'ep','_export_table':pa.Table.from_batches([batch])}
    with A.SD.request(self.root,A.pointer()['revision'],self.a,self.b,select,'EXPORT_SOURCE') as selected:
     f=selected['ep'];mask=(f.section+' · '+f.entry_point).isin(filters['entry']);entry_ids.update(f.loc[mask,'nm_id'])
  active=bool(filters.get('qclass') or filters.get('phrase') or filters.get('unknown_kind','Все')!='Все')
  if active and ('ep' in self.sources or self.model_scope):
   query_ids=set()
   for batch in ds.dataset(folder/'sq.parquet').scanner(filter=ds.field('week').isin([self.a,self.b]),batch_size=16384).to_batches():
    select=V.cohort_filters(filters)|{'_sources':('sq',),'_export_source':'sq','_export_table':pa.Table.from_batches([batch])}
    with A.SD.request(self.root,A.pointer()['revision'],self.a,self.b,select,'EXPORT_SOURCE') as selected:
     query_ids.update(V.query_filter(selected['sq'],filters).nm_id.unique())
  def frames(kind):
   for batch in ds.dataset(folder/(kind+'.parquet')).scanner(filter=ds.field('week').isin([self.a,self.b]),batch_size=16384,batch_readahead=1,fragment_readahead=1,use_threads=False).to_batches():
    select=V.cohort_filters(filters)|{'_sources':(kind,),'_export_source':kind,'_export_table':pa.Table.from_batches([batch])}
    with A.SD.request(self.root,A.pointer()['revision'],self.a,self.b,select,'EXPORT_SOURCE') as selected:
     q,e=A.rows(self.a,self.b,V.cohort_filters(filters));f=(q if self.model_scope else V.query_filter(q,filters)) if kind=='sq' else e
     if entry_ids is not None:f=f[f.nm_id.isin(entry_ids)]
     if query_ids is not None:f=f[f.nm_id.isin(query_ids)]
     if kind=='ep' and filters.get('entry') and not self.model_scope:f=f[(f.section+' · '+f.entry_point).isin(filters['entry'])]
     # Do not export the borrowed frame after its request lifetime.
     yield f
  with A.SD.GATE:return zip_frames({('TOP100' if kind=='sq' else 'Portrait')+'_all_filtered_rows.csv':frames(kind) for kind in self.sources},{'weeks':[self.a,self.b],'filters':filters,'model_membership_scope':self.model_scope})
@dataclass(frozen=True)
class MarketRaw:
 root:str;context_json:str;phrase:str;classes:tuple
 def build(self):
  from wb_search_r32 import market as M,analytics as A
  with A.SD.GATE:
   frame=M.filter_rows(M.comparison(M.Store(self.root),json.loads(self.context_json)),self.phrase,self.classes)
   frame=frame[[c for c in frame if 'share' not in c.lower() and 'доля' not in c.lower() and 'доли' not in c.lower()]]
   ctx=json.loads(self.context_json)
   return zip_frames({'WB_all_filtered_phrases.csv':(frame.iloc[i:i+4096] for i in range(0,max(1,len(frame)),4096))},{'category':ctx['category'],'current_start':ctx['a']['start'],'current_end':ctx['a']['end'],'base_start':ctx['b_start'],'base_end':ctx['b_end'],'phrase':self.phrase,'classes':self.classes,'warnings':ctx['warnings']})
