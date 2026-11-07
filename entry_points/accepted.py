"""Accepted R3.2 pure comparisons; no native WB imports or financial data."""
import pandas as pd
import numpy as np
from types import SimpleNamespace
from contextvars import ContextVar
from .classifier import LABELS
FLAGS=ContextVar("entry_flags")
def data():return {"ep_flags":FLAGS.get()}

METRICS=['clicks','carts','orders']

RU={'clicks':'Переходы','carts':'Корзины','orders':'Заказы','impressions':'Показы'}

DISPLAY_NAMES={'nm_id':'nmID WB','seller_article':'Артикул продавца','cm':'ЦМ','gender':'Гендер','collection':'Коллекция','product_type':'Тип товара','qclass':'Код класса','qrule':'Правило классификации','query_raw':'Исходная фраза','query_norm':'Нормализованная фраза','week':'Неделя','source':'Источник','brand':'Бренд в отчёте','brand_evidence':'Подтверждение бренда','row_anomaly':'Аномалия строки','anomaly_reason':'Причина аномалии','section':'Раздел','entry_point':'Точка входа'}

def safe_ratio(a,b):return a.div(b.where(b>0))*100

def aggregate(f,keys,extra=()):
 metrics=[c for c in METRICS+list(extra) if c in f]
 grouped=f.groupby(keys,dropna=False,sort=False)
 g=grouped[metrics].sum(min_count=1)
 g['anomaly']=grouped.row_anomaly.any().reindex(g.index).fillna(False)
 return g

def compare(f,a,b,keys,extra=()):
 """Absence in TOP100 is missing, with a separately named observed contribution."""
 columns=list(dict.fromkeys(list(keys)+['week','row_anomaly']+[c for c in METRICS+list(extra) if c in f]))
 projected=f[columns]
 x=aggregate(projected[projected.week==a],keys,extra);y=aggregate(projected[projected.week==b],keys,extra)
 idx=x.index.union(y.index);inc=idx.isin(x.index);both=inc&idx.isin(y.index)
 x,y=x.reindex(idx),y.reindex(idx);out=pd.DataFrame(index=idx)
 for c in METRICS+list(extra):
  if c not in x:continue
  out[RU[c]+' B']=y[c];out[RU[c]+' A']=x[c]
  out['Δ '+RU[c].lower()]=x[c]-y[c]
  out['Δ '+RU[c].lower()+', %']=(x[c]/y[c].where(y[c]>0)-1)*100
 out['Вклад в Δ выборки, шт.']=x.orders.fillna(0)-y.orders.fillna(0)
 out['Доля заказов B, %']=safe_ratio(y.orders,pd.Series(y.orders.sum(),index=idx))
 out['Доля заказов A, %']=safe_ratio(x.orders,pd.Series(x.orders.sum(),index=idx))
 out['Δ доли, п.п.']=out['Доля заказов A, %']-out['Доля заказов B, %']
 for m,stem in (('clicks','переходов'),('carts','корзин')):
  out['Доля '+stem+' B, %']=safe_ratio(y[m],pd.Series(y[m].sum(),index=idx))
  out['Доля '+stem+' A, %']=safe_ratio(x[m],pd.Series(x[m].sum(),index=idx))
  out['Δ доли '+stem+', п.п.']=out['Доля '+stem+' A, %']-out['Доля '+stem+' B, %']
 for suffix,g in (('A',x),('B',y)):
  out['Корзины / переходы '+suffix+', %']=safe_ratio(g.carts,g.clicks)
  out['Заказы / корзины '+suffix+', %']=safe_ratio(g.orders,g.carts)
  out['Заказы / переходы '+suffix+', %']=safe_ratio(g.orders,g.clicks)
  if 'impressions' in g:out['CTR '+suffix+', %']=safe_ratio(g.clicks,g.impressions)
 for label in ('Корзины / переходы','Заказы / корзины','Заказы / переходы')+ (('CTR',) if 'impressions' in x else ()):
  out['Δ '+label+', п.п.']=out[label+' A, %']-out[label+' B, %']
 out['Статус сравнения']=pd.Series(['Обе недели' if ok else 'Новый / база 0 или нет в TOP-100 B' if present else 'Вышел из доступной выборки TOP-100 A' for present,ok in zip(inc,both)],index=idx,dtype='string')
 # Retain anomalies of either week even on product/nmID/ЦМ slices and empty rows.
 out['Аномалия A']=x.anomaly.astype('boolean').fillna(False).astype(bool)
 out['Аномалия базы B']=y.anomaly.astype('boolean').fillna(False).astype(bool)
 out['Допустим для рейтинга']=~(out['Аномалия A']|out['Аномалия базы B']) & both
 out['Правило Δ']='A−B только при наличии обеих строк; вклад — изменение наблюдаемой выборки, не всего спроса'
 return out.reset_index()

def entry_table(e,a,b,keys=None):
 # Reuse the accepted B2 anomaly origin logic and source-pair flags. The R3
 # comparison adds explicit cart denominators and absolute click changes.
 columns=list(dict.fromkeys(['week','section','entry_point','row_anomaly']+METRICS+['impressions']+list(keys or [])))
 e=e[[c for c in columns if c in e]]
 flags=data()['ep_flags'];e=e.merge(flags.rename(columns={'anomaly':'pair_flag'}),on=['week','section','entry_point'],how='left',validate='many_to_one')
 e['row_anomaly']=e.row_anomaly|e.pair_flag.astype('boolean').fillna(False)
 r=compare(e,a,b,keys or ['section','entry_point'],['impressions'])
 if keys is None:
  r=r.rename(columns={'section':'Раздел','entry_point':'Точка входа'})
  r['Статус сравнения']=r['Статус сравнения'].str.replace('TOP-100','Портрет',regex=False)
  first=['Раздел','Точка входа','Переходы B','Переходы A','Δ переходы','Δ переходы, %','Заказы B','Заказы A','Δ заказы','Δ заказы, %','Доля заказов A, %','Δ доли, п.п.','Аномалия A','Аномалия базы B']
  r=r[first+[c for c in r if c not in first]]
 return r

SHARE={'orders':('Доля заказов','Δ доли, п.п.'),'clicks':('Доля переходов','Δ доли переходов, п.п.'),'carts':('Доля корзин','Δ доли корзин, п.п.')}

def fixed_shares(frame,denominator,a,b):
 out=frame.copy(deep=False)
 for m,(stem,delta) in SHARE.items():
  for w,s in ((a,'A'),(b,'B')):
   total=denominator.loc[denominator.week.eq(w),m].sum()
   out[stem+' '+s+', %']=out[A.RU[m]+' '+s]/total*100 if total>0 else np.nan
  out[delta]=out[stem+' A, %']-out[stem+' B, %']
 return out

def compact(frame,metric='orders',identifiers=None):
 if frame.empty:
  return pd.DataFrame(columns=['Фраза / источник','Ранее','Сейчас','Δ ед.','Δ %','Доля ранее, %','Доля сейчас, %','Δ доли, п.п.','База изменения'])
 stem,delta_share=SHARE[metric];lab=A.RU[metric]
 out=pd.DataFrame(index=frame.index)
 if identifiers:
  out['Фраза / источник']=frame[identifiers].fillna('Не определено').astype(str).agg(' · '.join,axis=1)
 else:
  col=next((c for c in ('Поисковый запрос','Класс','seller_article','query_raw') if c in frame),None)
  out['Фраза / источник']=frame[col] if col else pd.Series(dtype=str,index=frame.index)
 if 'Поисковый запрос' in frame and 'qclass' in frame:out['Класс запроса']=frame.qclass.map(LABELS)
 for target,source in [('Ранее',lab+' B'),('Сейчас',lab+' A'),('Δ ед.','Δ '+lab.lower()),('Δ %','Δ '+lab.lower()+', %'),('Доля ранее, %',stem+' B, %'),('Доля сейчас, %',stem+' A, %'),('Δ доли, п.п.',delta_share)]:
  out[target]=frame[source] if source in frame else np.nan
 out['База изменения']=np.select([out['Сейчас'].isna(),out['Ранее'].isna(),out['Ранее'].eq(0)],['Н/Д — нет строки в выборке A','Н/Д — нет строки в выборке B','Новый / база 0'],default='Обе строки доступны')
 if 'Аномалия A' in frame:out['Флаги A / B']=['A+B' if x and y else 'A' if x else 'B' if y else '' for x,y in zip(frame['Аномалия A'],frame['Аномалия базы B'])]
 return out

A=SimpleNamespace(RU=RU,DISPLAY_NAMES=DISPLAY_NAMES)
