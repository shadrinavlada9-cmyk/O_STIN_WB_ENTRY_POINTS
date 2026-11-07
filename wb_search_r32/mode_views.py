from public_search.temporary import make_directory
"""One mode at a time, unchanged aggregation formulas. Raw rows never survive the request."""
from dataclasses import dataclass
import json
import pandas as pd
from . import analytics as A,r31_views as V
from .classifier import VERSION
@dataclass(frozen=True)
class Anomalies:
 root:str
 revision:str
 a:str
 b:str
 filters_json:str
 mode:str
 def __call__(self):
  # Same source rows/flags and field union as concat(q_bad, e_bad), but never
  # keep both raw sources and two weeks alive during an Excel download.
  from pathlib import Path
  import tempfile,uuid,shutil
  import pyarrow as pa
  import pyarrow.dataset as ds
  from .export_ticket import Spool,Chunked
  folder=make_directory();chunks=[];size=0
  try:
   for kind,label in [('sq','TOP-100'),('ep','Портрет')]:
    # Preserve source order while processing at most 32K rows at a time.
    dataset=ds.dataset(Path(self.root)/'data'/self.revision/(kind+'.parquet'))
    scanner=dataset.scanner(filter=ds.field('week').isin([self.a,self.b]),batch_size=32768,batch_readahead=1,fragment_readahead=1,use_threads=False)
    seen=False
    def batches():
     nonlocal seen
     for batch in scanner.to_batches():
      seen=True;yield pa.Table.from_batches([batch])
     if not seen:yield pa.Table.from_batches([],schema=dataset.schema)
    for source_table in batches():
     filters=V.cohort_filters(json.loads(self.filters_json));filters.update(_sources=(kind,),_export_source=kind,_export_table=source_table)
     with A.SD.request(self.root,self.revision,self.a,self.b,filters,'EXPORT_SOURCE') as selected:
      raw=selected[kind]
      mask=pd.Series(True,index=raw.index)
      for c in ('gender','collection','product_type','seller_article','cm'):
       if filters.get(c):mask &= raw[c].isin(filters[c])
      if filters.get('nm_id'):mask &= raw.nm_id.astype(str).isin(list(map(str,filters['nm_id'])))
      frame=raw[mask & raw.row_anomaly].assign(source=label)
      path=folder/(str(len(chunks))+'.parquet');frame.to_parquet(path,index=False)
      chunks.append(Spool(path,tuple(frame.columns),len(frame)));size+=path.stat().st_size
      if size>256*1024**2:raise ValueError('Anomaly export exceeds its explicit disk budget')
      del frame,raw,mask
   return Chunked(folder,chunks)
  except BaseException:
   shutil.rmtree(folder,ignore_errors=True);raise
def collapsed(q,a,b):
 f=q.groupby(['week','qclass'],dropna=False)[A.METRICS].sum(min_count=1).reset_index()
 f=f.merge(q.groupby(['week','qclass'],dropna=False).row_anomaly.any().reset_index(),validate='one_to_one')
 f['qclass']=f.qclass.replace({'BRAND_PURE':'BRAND_CATEGORY'})
 return A.class_table(f,a,b)
def bundle(a,b,filters,mc,mo,mode):
 if A.SD.CURRENT.get() is None:
  with A.request(a,b,filters,mode):return bundle(a,b,filters,mc,mo,mode)
 filters=filters or {};cohort=V.cohort_filters(filters);q,e=A.rows(a,b,cohort);s={}
 active=bool(filters.get('qclass') or filters.get('phrase') or filters.get('unknown_kind','Все')!='Все')
 viewq=V.query_filter(q,filters) if active else q
 if mode=='Точки входа':
  scoped=e[e.nm_id.isin(viewq.nm_id.unique())] if active else e
  s['Точки входа']=V.fixed_shares(A.entry_table(scoped,a,b),e,a,b)
  if filters.get('entry'):
   f=s['Точки входа'];s['Точки входа']=f[(f['Раздел']+' · '+f['Точка входа']).isin(filters['entry'])]
 elif mode in ('Брендовые запросы','Все запросы TOP-100'):
  ph=A.phrases(q,a,b);q.attrs.update(a=a,b=b)
  s['Сравнительная сводка']=V.fixed_shares(A.class_table(viewq,a,b),q,a,b)
  total=V.fixed_shares(collapsed(viewq,a,b),q,a,b)
  total.loc[total.qclass.eq('BRAND_CATEGORY'),'Класс']='Бренд O’STIN · выбранные фразы' if active else 'Брендовые O’STIN всего (чистый + категория)'
  s['Бренд и небренд']=total
  s['Все запросы TOP100']=V.query_filter(ph,filters)
  brand=ph[ph.qclass.isin(['BRAND_PURE','BRAND_CATEGORY'])]
  s['Брендовые запросы']=V.query_filter(V.brand_shares(brand,q),filters)
  ranking=ph[ph['Допустим для рейтинга']&ph['Переходы A'].ge(mc)&ph['Заказы A'].ge(mo)]
  s['Лучшие конверсии']=V.query_filter(ranking.sort_values(['Заказы / переходы A, %','Переходы A'],ascending=False).head(30),filters)
  traffic=ph[ph['Допустим для рейтинга']&ph['Переходы A'].ge(mc)];median=traffic['Заказы / переходы A, %'].median()
  s['Трафик слабая конверсия']=V.query_filter(traffic[traffic['Заказы / переходы A, %']<median].sort_values('Переходы A',ascending=False).head(30),filters)
  s['Новые в TOP100']=V.query_filter(ph[ph['Статус сравнения'].str.startswith('Новый')].sort_values('Заказы A',ascending=False),filters)
  s['Вышли из TOP100']=V.query_filter(ph[ph['Статус сравнения'].str.startswith('Вышел')].sort_values('Заказы B',ascending=False),filters)
 elif mode=='Какие товары требуют внимания':
  models=V.model_structure(A.model_table(q,e,a,b,mc,mo))
  ranked=models[models['Допустим для рейтинга']&models['Достаточно наблюдений']&models['Возможность'].ne('Недостаточно сопоставимых данных')]
  top=ranked.sort_values('Переходы A',ascending=False).head(10)
  s['Топ возможностей']=top[[c for c in top if not c.startswith(('Доля бренд','Доля небренд','Δ структуры'))]]
  if active:models=models[models.nm_id.isin(viewq.nm_id.unique())]
  if filters.get('entry'):
   selected=e[(e.section+' · '+e.entry_point).isin(filters['entry'])];models=models[models.nm_id.isin(selected.nm_id.unique())]
  s['Товары и возможности']=models
  s['Структура спроса моделей']=models[['nm_id','seller_article']+[c for c in models if c.startswith(('Доля бренд','Доля небренд','Δ структуры'))]]
 else:raise ValueError('Unsupported O’STIN mode')
 flags=A.data()['ep_flags'];s['Аномалии показов']=flags[flags.week.isin([a,b])&flags.anomaly].copy().assign(Правило='Показы ≥1 млн и >10× медианы прошлых недель; сохранены SHA-подтверждённые флаги B2',Показатели='Показы / CTR не подтверждены; исходные значения не обрезаются')
 s['Аномалии строк']=Anomalies(str(A.ROOT),A.pointer()['revision'],a,b,json.dumps(cohort,sort_keys=True),mode)
 s['Источники']=pd.DataFrame([{k:v for k,v in r.items() if k in ('week','kind','file','sha256','start','end','local_modified','checked_at','accepted_rows','reference_conflict_rows')} for r in A.data()['inventory']])
 s['Влияние аномалий']=A.SD.anomaly_impact(A.ROOT,A.pointer()['revision'],a,b,cohort)
 totals=s['Влияние аномалий'].attrs['source_totals']
 s['Диагностика R31']=pd.DataFrame([{'Показатель':'Фильтры','Значение':str(filters)},{'Показатель':'Знаменатели долей','Значение':'Полный отчёт после товарных фильтров; текст/класс/точка входа фильтруют отображение, не пересчитывают базу доли. Брендовые фразы — доля внутри полного брендового сегмента выбранных моделей.'},{'Показатель':'Модели','Значение':'Показатели модели по всем её фразам; текст/класс отбирает модели, а не подменяет структуру их спроса.'}])
 for source,name in (('TOP100','TOP-100'),('Портрет','Портрет')):
  for w,label in ((a,'A'),(b,'B')):
   for metric in A.METRICS:s['Диагностика R31'].loc[len(s['Диагностика R31'])]=[source+' '+label+' знаменатель '+metric,str(totals.get(name+'|'+w+'|'+metric,0))]
 return s
