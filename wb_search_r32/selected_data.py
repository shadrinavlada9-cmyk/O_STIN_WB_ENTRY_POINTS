"""Request-owned projected Arrow scans; no process-wide mutable DataFrames or history cache."""
from pathlib import Path
from collections.abc import Mapping
import contextvars,contextlib,json,threading,gc
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
UNKNOWN='Не определено'
CURRENT=contextvars.ContextVar('wb_search_request',default=None)
GATE=threading.RLock()
def arrow_type(t):return pd.StringDtype(storage='pyarrow') if pa.types.is_string(t) or pa.types.is_large_string(t) else None
def dim_frame(folder):
 d=ds.dataset(folder/'dim.parquet').to_table(columns=['nm_id','cm','cm_method','gender','collection','product_type']).to_pandas(types_mapper=arrow_type)
 d['gender']=d.gender.replace({'Wmn':'OS WMN','Men':'OS MEN','Kids':'OS KIDS','WOMEN':'OS WMN','MEN':'OS MEN','KIDS':'OS KIDS','WMN':'OS WMN','OS WOMEN':'OS WMN'})
 for c in ('gender','collection','product_type','cm'):d[c]=d[c].fillna(UNKNOWN).replace({'':UNKNOWN,'nan':UNKNOWN})
 d.loc[d.cm_method.astype(str).str.contains('AMBIG|NOT_IN',case=False),'cm']=UNKNOWN
 assert d.nm_id.is_unique
 return d
class Selected(Mapping):
 def __init__(self,root,revision,weeks,filters=None,mode=None):
  self.root=Path(root);self.folder=self.root/'data'/revision;self.weeks=tuple(dict.fromkeys(weeks));self.filters=filters or {};self.mode=mode;self.frames={};self.dim=None
  query_active=any(self.filters.get(k) for k in ('qclass','phrase')) or self.filters.get('unknown_kind','Все')!='Все'
  self.aggregate_q=mode in ('Брендовые запросы','Все запросы TOP-100')
  self.aggregate_e=mode=='Точки входа' and not query_active
 def __iter__(self):return iter(('sq','ep','dim','ep_flags','inventory'))
 def __len__(self):return 5
 def __getitem__(self,key):
  if key in self.frames:return self.frames[key]
  if key=='inventory':value=json.loads((self.folder/'inventory.json').read_text(encoding='utf8'))
  elif key=='dim':
   if self.dim is None:self.dim=dim_frame(self.folder)
   value=self.dim
  elif key=='ep_flags':value=ds.dataset(self.folder/'ep_flags.parquet').to_table(filter=ds.field('week').isin(self.weeks)).to_pandas(types_mapper=arrow_type)
  else:value=self.scan(key)
  self.frames[key]=value;return value
 def scan(self,key):
  from .classifier import apply_frame
  # Export-only bounded batches use exactly the same enrichment/flag pipeline.
  # Ordinary UI requests continue to scan the immutable prepared source.
  export_table=self.filters.get('_export_table')
  dataset=ds.dataset(export_table) if export_table is not None and key==self.filters.get('_export_source') else ds.dataset(self.folder/(key+'.parquet'))
  condition=ds.field('week').isin(self.weeks)
  if key not in self.filters.get('_sources',('sq','ep')):condition=condition & (ds.field('week')=='__not_requested__')
  if key=='sq' and '_raw_queries' in self.filters:condition=condition & ds.field('query_raw').isin(self.filters['_raw_queries'])
  if key=='ep' and self.filters.get('_entry_pairs'):
   exact=None
   for section,point in self.filters['_entry_pairs']:
    pair=(ds.field('section')==section)&(ds.field('entry_point')==point);exact=pair if exact is None else exact|pair
   condition=condition & exact
  if (key=='ep' and self.mode in ('Брендовые запросы','Все запросы TOP-100')) or (key=='sq' and self.mode=='Точки входа' and not any(self.filters.get(k) for k in ('qclass','phrase')) and self.filters.get('unknown_kind','Все')=='Все'):
   condition=condition & (ds.field('week')=='__not_requested__')
  dim=self['dim'];selected=dim
  for c in ('gender','collection','product_type','cm'):
   # product_type may fall back to subject for an unmapped nmID. Apply that
   # predicate after enrichment, rather than excluding unknown-source products.
   if c!='product_type' and self.filters.get(c):selected=selected[selected[c].isin(self.filters[c])]
  if self.filters.get('nm_id'):condition=condition & ds.field('nm_id').isin([int(n) for n in self.filters['nm_id'] if str(n).isdigit()])
  if self.filters.get('seller_article'):condition=condition & ds.field('seller_article').isin(self.filters['seller_article'])
  if len(selected)!=len(dim) and UNKNOWN not in sum([list(self.filters.get(c,[])) for c in ('gender','collection','cm')],[]):condition=condition & ds.field('nm_id').isin(selected.nm_id.tolist())
  # UI needs metrics/keys; verbose source attributes are deferred to the export.
  full=self.mode in (None,'EXPORT_SOURCE')
  columns=[c for c in dataset.schema.names if full or c not in ('name','brand','brand_evidence','visibility','avg_pos','query_norm','qclass','qrule')]
  aggregate=(key=='sq' and self.aggregate_q) or (key=='ep' and self.aggregate_e)
  product_filtered=any(self.filters.get(c) for c in ('nm_id','seller_article','gender','collection','product_type','cm'))
  if aggregate and not product_filtered:columns=[c for c in columns if c not in ('nm_id','seller_article','subject')]
  f=dataset.to_table(columns=columns,filter=condition).to_pandas(types_mapper=arrow_type)
  before=f[['clicks','carts','orders']].sum();count=len(f)
  if key=='sq':f=apply_frame(f)
  if not aggregate or product_filtered:
   f=f.merge(dim,on='nm_id',how='left',validate='many_to_one')
   for c in ('cm','cm_method','gender','collection','product_type'):f[c]=f[c].fillna(UNKNOWN)
   f['product_type']=f.product_type.where(f.product_type.ne(UNKNOWN),f.subject.fillna(UNKNOWN))
  assert len(f)==count and f[['clicks','carts','orders']].sum().equals(before)
  if aggregate and product_filtered:
   for c in ('gender','collection','product_type','seller_article','cm'):
    if self.filters.get(c):f=f[f[c].isin(self.filters[c])]
   if self.filters.get('nm_id'):f=f[f.nm_id.astype(str).isin(list(map(str,self.filters['nm_id'])))]
   count=len(f);before=f[['clicks','carts','orders']].sum()
  reason=pd.Series('',index=f.index,dtype='string[pyarrow]') if full else None
  bad=f.row_anomaly.copy()
  for condition,label in ((f.orders>f.clicks,'заказы > переходов'),(f.orders>f.carts,'заказы > корзин'),(f.carts>f.clicks,'корзины > переходов')):
   bad=bad|condition
   if full:reason=reason.where(~condition,reason+label+'; ')
  if 'impressions' in f:
   bad=bad|f.clicks.gt(f.impressions)
   if full:reason=reason.where(~(f.clicks>f.impressions),reason+'переходы > показов; ')
  for c in ('source_ctr','source_cart_cr','source_order_cr'):
   if c in f:
    bad=bad|f[c].gt(100)
    if full:reason=reason.where(~(f[c]>100),reason+c+' >100%; ')
  if full:f['anomaly_reason']=reason
  else:
   f=f.drop(columns=[c for c in ('subject','cm_method','source_ctr','source_cart_cr','source_order_cr','active_classifier_version') if c in f])
  f['row_anomaly']=bad
  for c in f.select_dtypes(include=['object','string']).columns:f[c]=f[c].astype('string[pyarrow]')
  assert len(f)==count and f[['clicks','carts','orders']].sum().equals(before)
  if aggregate:
   keys=['week','query_norm','qclass','query_raw','frequency','qrule'] if key=='sq' else ['week','section','entry_point']
   metrics=['clicks','carts','orders']+(['impressions'] if 'impressions' in f else [])
   grouped=f.groupby(keys,dropna=False,sort=False);out=grouped[metrics].sum(min_count=1);out['row_anomaly']=grouped.row_anomaly.any()
   f=out.reset_index();assert f[['clicks','carts','orders']].sum().equals(before)
  return f
 def clear(self):self.frames.clear();self.dim=None
@contextlib.contextmanager
def request(root,revision,a,b,filters=None,mode=None):
 # Single heavy Search computation per worker. The gate is not a data cache;
 # each session owns every DataFrame and releases it at the end of the request.
 with GATE:
  source=Selected(root,revision,(a,b),filters,mode);token=CURRENT.set(source)
  try:yield source
  finally:
   source.clear();CURRENT.reset(token);release()
def release():
 gc.collect()
 # The Arrow allocator may retain large freed buffers after a request. This
 # releases only unused allocator pages; live frames owned by callers are intact.
 pa.default_memory_pool().release_unused()
 # glibc can retain freed pandas/numpy allocations independently of Arrow.
 # malloc_trim releases only unused heap pages, never live objects. Other
 # platforms/allocators simply keep their normal behaviour.
 if __import__('sys').platform.startswith('linux'):
  try:
   import ctypes
   trim=ctypes.CDLL(None).malloc_trim;trim.argtypes=[ctypes.c_size_t];trim.restype=ctypes.c_int;trim(0)
  except (AttributeError,OSError):pass
def options(root,revision):
 folder=Path(root)/'data'/revision;dim=dim_frame(folder);frames=[]
 for kind in ('sq','ep'):
  t=ds.dataset(folder/(kind+'.parquet')).to_table(columns=['nm_id','seller_article','subject'])
  t=t.group_by(t.column_names).aggregate([])
  f=t.to_pandas(types_mapper=arrow_type).merge(dim,on='nm_id',how='left',validate='many_to_one')
  for c in ('cm','gender','collection','product_type'):f[c]=f[c].fillna(UNKNOWN)
  f['product_type']=f.product_type.where(f.product_type.ne(UNKNOWN),f.subject.fillna(UNKNOWN));frames.append(f)
 f=pd.concat(frames,ignore_index=True).drop_duplicates()
 t=ds.dataset(folder/'ep.parquet').to_table(columns=['section','entry_point']);e=t.group_by(t.column_names).aggregate([]).to_pandas(types_mapper=arrow_type)
 return {c:sorted(f[c].astype(str).unique()) for c in ('nm_id','seller_article','cm','gender','collection','product_type')}|{'entry':sorted((e.section+' · '+e.entry_point).unique())}

def anomaly_impact(root,revision,a,b,filters):
 folder=Path(root)/'data'/revision;dim=dim_frame(folder);partials=[];totals={}
 for kind,name in (('sq','TOP-100'),('ep','Портрет')):
  dataset=ds.dataset(folder/(kind+'.parquet'))
  columns=[c for c in ('week','nm_id','seller_article','subject','row_anomaly','clicks','carts','orders','impressions','source_ctr','source_cart_cr','source_order_cr') if c in dataset.schema.names]
  scanner=dataset.scanner(columns=columns,filter=ds.field('week').isin([a,b]),batch_size=32768)
  for batch in scanner.to_batches():
   f=batch.to_pandas(types_mapper=arrow_type).merge(dim,on='nm_id',how='left',validate='many_to_one')
   for c in ('cm','gender','collection','product_type'):f[c]=f[c].fillna(UNKNOWN)
   f['product_type']=f.product_type.where(f.product_type.ne(UNKNOWN),f.subject.fillna(UNKNOWN))
   for c in ('gender','collection','product_type','seller_article','cm'):
    if filters.get(c):f=f[f[c].isin(filters[c])]
   if filters.get('nm_id'):f=f[f.nm_id.astype(str).isin(list(map(str,filters['nm_id'])))]
   for week,values in f.groupby('week')[['clicks','carts','orders']].sum().iterrows():
    for metric,value in values.items():
     key=name+'|'+str(week)+'|'+metric;scalar=value.item() if hasattr(value,'item') else value;totals[key]=totals.get(key,0)+scalar
   bad=f.row_anomaly|f.orders.gt(f.clicks)|f.orders.gt(f.carts)|f.carts.gt(f.clicks)
   if 'impressions' in f:bad=bad|f.clicks.gt(f.impressions)
   for c in ('source_ctr','source_cart_cr','source_order_cr'):
    if c in f:bad=bad|f[c].gt(100)
   f=f[bad]
   if f.empty:continue
   g=f.groupby('week')[['clicks','carts','orders']].sum(min_count=1);g['Аномальных строк']=f.groupby('week').size();g['source']=name;partials.append(g.reset_index())
 out=pd.concat(partials).groupby(['source','week'],dropna=False)[['clicks','carts','orders','Аномальных строк']].sum(min_count=1).reset_index() if partials else pd.DataFrame(columns=['source','week','clicks','carts','orders','Аномальных строк'])
 out.attrs['source_totals']=totals
 return out
