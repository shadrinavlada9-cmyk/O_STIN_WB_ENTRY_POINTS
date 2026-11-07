"""R3 business views over WB weekly rows. No finance, ad or stock calculations."""
from pathlib import Path
from functools import lru_cache
import io,json,importlib.util,sys,os
import pandas as pd
import numpy as np
from .classifier import LABELS,VERSION,apply_frame
ROOT=Path(os.environ['WB_SEARCH_DATA_ROOT']).resolve()
METRICS=['clicks','carts','orders']
RU={'clicks':'Переходы','carts':'Корзины','orders':'Заказы','impressions':'Показы'}
UNKNOWN='Не определено'
DISPLAY_NAMES={'nm_id':'nmID WB','seller_article':'Артикул продавца','cm':'ЦМ','gender':'Гендер','collection':'Коллекция','product_type':'Тип товара','qclass':'Код класса','qrule':'Правило классификации','query_raw':'Исходная фраза','query_norm':'Нормализованная фраза','week':'Неделя','source':'Источник','brand':'Бренд в отчёте','brand_evidence':'Подтверждение бренда','row_anomaly':'Аномалия строки','anomaly_reason':'Причина аномалии','section':'Раздел','entry_point':'Точка входа'}
def display(f):return f.rename(columns=DISPLAY_NAMES)

def pointer():return json.loads((ROOT/'data/current.json').read_text(encoding='utf8'))
from . import selected_data as SD

def load(revision):return SD.CURRENT.get() or SD.Selected(ROOT,revision,tuple(weeks()))
load.cache_clear=lambda:None
def data():return load(pointer()['revision'])
def weeks():return pointer()['weeks']
def options():return SD.options(ROOT,pointer()['revision'])
def request(a,b,filters=None,mode=None):return SD.request(ROOT,pointer()['revision'],a,b,filters,mode)

def rows(a,b,filters=None):
 current=SD.CURRENT.get()
 if current is not None and (current.aggregate_q or current.aggregate_e):return current['sq'],current['ep']
 filters=filters or {};d=SD.CURRENT.get() or SD.Selected(ROOT,pointer()['revision'],(a,b),filters)
 # Selected owns these frames for this one request; Arrow already applied the
 # requested week predicate. Reapplying an all-True mask copied both full raw
 # sources twice in model/detail modes. No global/shared mutable cache is used.
 q,e=d['sq'],d['ep']
 if not set(d.weeks).issubset({a,b}):q=q[q.week.isin([a,b])];e=e[e.week.isin([a,b])]
 def product(f):
  if not any(filters.get(c) for c in ('gender','collection','product_type','seller_article','cm','nm_id')):return f
  mask=pd.Series(True,index=f.index)
  for c in ('gender','collection','product_type','seller_article','cm'):
   if filters.get(c):mask &= f[c].isin(filters[c])
  if filters.get('nm_id'):mask &= f.nm_id.astype(str).isin(list(map(str,filters['nm_id'])))
  return f if mask.all() else f[mask]
 q,e=product(q),product(e)
 if filters.get('qclass'):q=q[q.qclass.isin(filters['qclass'])]
 if filters.get('phrase'):q=q[q.query_raw.str.casefold().str.contains(str(filters['phrase']).casefold(),regex=False,na=False)]
 if filters.get('qclass') or filters.get('phrase'):e=e[e.nm_id.isin(q.nm_id.unique())]
 if filters.get('entry'):
  e=e[(e.section+' · '+e.entry_point).isin(filters['entry'])];q=q[q.nm_id.isin(e.nm_id.unique())]
 return q,e

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

def phrases(q,a,b):
 r=compare(q,a,b,['query_norm','qclass'])
 names=q.groupby(['query_norm','qclass']).query_raw.agg(lambda s:' | '.join(sorted(set(map(str,s)))))
 assert names.index.is_unique
 index=pd.MultiIndex.from_frame(r[['query_norm','qclass']])
 r['Исходные фразы']=names.reindex(index).to_numpy()
 f=q.loc[q.week==a,['query_norm','qclass','frequency']].groupby(['query_norm','qclass']).frequency.agg(['min','max','nunique']).reset_index()
 f=f.rename(columns={'min':'Частотность WB min A','max':'Частотность WB max A','nunique':'Разных значений частоты A'})
 f=f.set_index(['query_norm','qclass']);assert f.index.is_unique
 for c in f:r[c]=f[c].reindex(index).to_numpy()
 r['Класс']=r.qclass.map(LABELS);r['Частотность']='Не суммируется; диапазон значений WB по фразе'
 r=r.rename(columns={'query_norm':'Поисковый запрос'})
 first=['Поисковый запрос','Класс','Заказы B','Заказы A','Δ заказы','Δ заказы, %','Переходы B','Переходы A','Заказы / переходы A, %','Статус сравнения','Аномалия A','Аномалия базы B']
 return r[first+[c for c in r if c not in first]]

def class_table(q,a,b):
 r=compare(q,a,b,['qclass']);r['Класс']=r.qclass.map(LABELS)
 r['База доли']='Все заказы/переходы выбранной выборки TOP-100 O’STIN, не весь поиск WB'
 for m in ('clicks','orders'):
  for w,s in ((a,'A'),(b,'B')):
   total=q.loc[q.week==w,m].sum();r['Доля '+{'clicks':'переходов','orders':'заказов'}[m]+' '+s+', %']=r[RU[m]+' '+s]/total*100 if total else np.nan
 first=['Класс','Заказы B','Заказы A','Δ заказы','Δ заказы, %','Доля заказов B, %','Доля заказов A, %','Δ доли, п.п.','Переходы B','Переходы A']
 return r[first+[c for c in r if c not in first]]

def model_table(q,e,a,b,min_clicks=100,min_orders=3):
 # All sizes are already contained in the nmID/seller-article source. No size join.
 cols=['nm_id','seller_article','cm','gender','collection','product_type']
 f=q.groupby(['week']+cols,dropna=False)[METRICS].sum(min_count=1).reset_index()
 f=f.merge(q.groupby(['week']+cols,dropna=False).row_anomaly.any().reset_index(),on=['week']+cols,validate='one_to_one')
 r=compare(f,a,b,cols)
 for cls,label in ((['BRAND_PURE','BRAND_CATEGORY'],'Бренд'),(['NON_BRAND'],'Небренд')):
  z=q[q.qclass.isin(cls)]
  g=z.groupby(['nm_id','week'])[METRICS].sum()
  for w,s in ((a,'A'),(b,'B')):
   for m in ('orders','clicks'):
    series=g.xs(w,level='week')[m] if w in g.index.get_level_values('week') else pd.Series(dtype=float)
    r[label+' '+RU[m].lower()+' '+s]=r.nm_id.map(series)
 # Independently sourced portrait context, never added to TOP-100 orders.
 pg=e.groupby(['nm_id','week']).orders.sum()
 for w,s in ((a,'A'),(b,'B')):
  series=pg.xs(w,level='week') if w in pg.index.get_level_values('week') else pd.Series(dtype=float)
  r['Заказы Портрет '+s]=r.nm_id.map(series)
 from .deterministic import leading_queries,leading_entries
 leading=leading_queries(q,a)
 r['Основные фразы A']=r.nm_id.map(leading)
 topentry=leading_entries(e,a)
 r['Главная точка входа A']=r.nm_id.map(topentry)
 r['Достаточно наблюдений']=(r['Переходы A']>=min_clicks)&(r['Заказы A']>=min_orders)
 r['Возможность']='Недостаточно сопоставимых данных';r['Проверка']='Проверить полноту и размер выборки; не менять бюджет автоматически'
 clean=r['Допустим для рейтинга']&r['Достаточно наблюдений']
 # Peer median is diagnostic, not an approved advertising budget threshold.
 peers=r[clean].groupby(['product_type','gender','collection'])['Заказы / переходы A, %'].agg(['median','count'])
 r=r.merge(peers.rename(columns={'median':'Медиана сопоставимой группы, %','count':'Моделей в группе'}),left_on=['product_type','gender','collection'],right_index=True,how='left',validate='many_to_one')
 enough=r['Моделей в группе'].fillna(0)>=5
 weak=clean&enough&(r['Заказы / переходы A, %']<r['Медиана сопоставимой группы, %'])&(r['Небренд переходы A']>=min_clicks)
 strong=clean&enough&(r['Заказы / переходы A, %']>=r['Медиана сопоставимой группы, %'])&(r['Небренд заказы A']>r['Небренд заказы B'])&(r['Небренд переходы A']>=min_clicks)&(r['Небренд заказы A']>=min_orders)&(r['Небренд заказы B']>0)
 r.loc[weak,['Возможность','Проверка']]=['Небрендовый трафик; конверсия среза ниже медианы группы','Проверить карточку, размеры, цену и доступность; причина не доказана']
 r.loc[strong,['Возможность','Проверка']]=['Рост небрендового спроса при сильной конверсии','Рассмотреть улучшение присутствия в поиске; рекламный бюджет отдельно по эффективности РК']
 drop=clean&(r['Бренд заказы A']<r['Бренд заказы B'])
 r.loc[drop,['Возможность','Проверка']]=['Снижение брендовых заказов TOP-100','Разобрать конкретные брендовые фразы; выпадение из TOP-100 не равно исчезновению спроса']
 r['Ограничение группы']=np.where(enough,'Тип × гендер × коллекция, ≥5 моделей','Сопоставимая группа <5: вывод об относительной эффективности ограничен')
 r.loc[~r['Допустим для рейтинга'],'Возможность']='Неподтверждённая аномалия / неполная база — вне рейтинга'
 r['Факт и сравнение']=r.apply(lambda x:f"TOP-100 A: {x['Переходы A']:g} переходов, {x['Заказы A']:g} заказов; B: {x['Переходы B']:g} / {x['Заказы B']:g}; Δ {x['Δ заказы']:g} шт.; брендовые заказы B→A {x['Бренд заказы B']:g}→{x['Бренд заказы A']:g}; небрендовые заказы {x['Небренд заказы B']:g}→{x['Небренд заказы A']:g}, переходы A {x['Небренд переходы A']:g}; заказы/переходы A {x['Заказы / переходы A, %']:.2f}%, медиана группы {x['Медиана сопоставимой группы, %']:.2f}%",axis=1) if len(r) else pd.Series(dtype=str)
 return r

def bundle(a,b,filters=None,min_clicks=100,min_orders=3):
 if SD.CURRENT.get() is None:
  with request(a,b,filters):return bundle(a,b,filters,min_clicks,min_orders)
 q,e=rows(a,b,filters);classes=class_table(q,a,b);ph=phrases(q,a,b);ep=entry_table(e,a,b)
 models=model_table(q,e,a,b,min_clicks,min_orders)
 anomaly=pd.concat([q[q.row_anomaly].assign(source='TOP-100'),e[e.row_anomaly].assign(source='Портрет')],ignore_index=True)
 sourceflags=data()['ep_flags'];sourceflags=sourceflags[sourceflags.week.isin([a,b])&sourceflags.anomaly]
 diag=pd.DataFrame([{'Параметр':'Неделя A','Значение':a},{'Параметр':'База сравнения B','Значение':b},{'Параметр':'Фильтры','Значение':json.dumps(filters or {},ensure_ascii=False)},{'Параметр':'Версия классификатора','Значение':VERSION},{'Параметр':'Минимальная статистика','Значение':f'{min_clicks} переходов, {min_orders} заказов; не доказательство устойчивости. Кандидат роста небрендового спроса дополнительно требует этих минимумов по небрендовому сегменту A и положительной базы B.'},{'Параметр':'Нет следующей недели','Значение':pointer()['missing_next']},{'Параметр':'Методика','Значение':'TOP-100 по переходам на товар; Портрет отдельно. CR WB=заказы/корзины; сквозная=заказы/переходы. Платное/органика не разделяются. Частота не суммируется. Δ отсутствующих строк — Н/Д, вклад только в наблюдаемую выборку.'}])
 ranking=ph[ph['Допустим для рейтинга']&(ph['Переходы A']>=min_clicks)&(ph['Заказы A']>=min_orders)]
 brand=ph[ph.qclass.isin(['BRAND_PURE','BRAND_CATEGORY'])]
 model_rank=models[models['Допустим для рейтинга']&models['Достаточно наблюдений']&models['Возможность'].ne('Недостаточно сопоставимых данных')]
 sheets={'Сравнительная сводка':classes,'Точки входа':ep,'Брендовые запросы':brand,'Все запросы TOP100':ph,'Товары и возможности':models,'Топ возможностей':model_rank.sort_values('Переходы A',ascending=False).head(10),'Рост запросов':ranking.sort_values('Δ заказы',ascending=False).head(30),'Падение запросов':ranking.sort_values('Δ заказы').head(30),'Аномалии строк':anomaly,'Аномалии показов':sourceflags,'Диагностика':diag,'Источники':pd.DataFrame([{k:v for k,v in r.items() if k in ('week','kind','file','sha256','start','end','local_modified','checked_at','accepted_rows','reference_conflict_rows')} for r in data()['inventory']])}
 overall=q[['week','qclass','row_anomaly']+METRICS].copy();overall['qclass']=overall.qclass.replace({'BRAND_PURE':'BRAND_CATEGORY'})
 total=class_table(overall,a,b);total.loc[total.qclass.eq('BRAND_CATEGORY'),'Класс']='Брендовые O’STIN всего (чистый + категория)'
 sheets['Бренд и небренд']=total
 sheets['Лучшие конверсии']=ranking.sort_values(['Заказы / переходы A, %','Переходы A'],ascending=False).head(30)
 clean_brand=ranking[ranking.qclass.isin(['BRAND_PURE','BRAND_CATEGORY'])]
 sheets['Бренд рост']=clean_brand.sort_values('Δ заказы',ascending=False).head(30)
 sheets['Бренд падение']=clean_brand.sort_values('Δ заказы').head(30)
 traffic=ph[ph['Допустим для рейтинга']&(ph['Переходы A']>=min_clicks)]
 median=traffic['Заказы / переходы A, %'].median()
 sheets['Трафик слабая конверсия']=traffic[traffic['Заказы / переходы A, %']<median].sort_values('Переходы A',ascending=False).head(30)
 sheets['Новые в TOP100']=ph[ph['Статус сравнения'].str.startswith('Новый')].sort_values('Заказы A',ascending=False)
 sheets['Вышли из TOP100']=ph[ph['Статус сравнения'].str.startswith('Вышел')].sort_values('Заказы B',ascending=False)
 impact=anomaly.groupby(['source','week'],dropna=False)[METRICS].sum(min_count=1).reset_index()
 impact=impact.merge(anomaly.groupby(['source','week'],dropna=False).size().rename('Аномальных строк').reset_index(),on=['source','week'],validate='one_to_one')
 sheets['Влияние аномалий']=impact
 sheets['Аномалии показов']=sourceflags.copy().assign(Правило='Показы ≥1 млн и >10× медианы прошлых недель; сохранены SHA-подтверждённые флаги B2',Показатели='Показы / CTR не подтверждены; исходные значения не обрезаются')
 top=sheets['Топ возможностей'];first=['seller_article','nm_id','Возможность','Заказы B','Заказы A','Δ заказы','Переходы A','Заказы / переходы A, %','Факт и сравнение','Проверка','Основные фразы A','Главная точка входа A','Ограничение группы']
 sheets['Топ возможностей']=top[first+[c for c in top if c not in first]]
 return sheets

def excel(sheets):
 import xlsxwriter,math
 b=io.BytesIO()
 with xlsxwriter.Workbook(b,{'constant_memory':True,'strings_to_formulas':False,'strings_to_urls':False,'nan_inf_to_errors':False}) as writer:
  header=writer.add_format({'bold':True,'bg_color':'#EEE6F5','bottom':1})
  for name,table in sheets.items():
   ws=writer.add_worksheet(name[:31]);ws.write_row(0,0,list(display(table).columns),header)
   for i,row in enumerate(table.itertuples(index=False,name=None),1):
    for j,v in enumerate(row):
     if v is None or (isinstance(v,(float,np.floating)) and not math.isfinite(v)) or v is pd.NA:continue
     if isinstance(v,(list,dict,tuple)):v=str(v)
     if isinstance(v,np.generic):v=v.item()
     ws.write(i,j,v)
   ws.freeze_panes(1,0);ws.autofilter(0,0,len(table),max(0,len(table.columns)-1));ws.set_column(0,max(0,len(table.columns)-1),18)
 return b.getvalue()
