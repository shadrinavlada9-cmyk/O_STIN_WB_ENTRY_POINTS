"""Thin R3.1 presentation/share layer over the accepted R3 calculations."""
import pandas as pd
import numpy as np
import json
from functools import lru_cache
from . import analytics as A
from .classifier import LABELS,normalize,classify
METRICS={'Заказы':'orders','Переходы':'clicks','Корзины':'carts'}
SHARE={'orders':('Доля заказов','Δ доли, п.п.'),'clicks':('Доля переходов','Δ доли переходов, п.п.'),'carts':('Доля корзин','Δ доли корзин, п.п.')}
PRODUCT_KEYS=('gender','collection','product_type','seller_article','nm_id','cm')
def cohort_filters(filters):return {k:v for k,v in (filters or {}).items() if k in PRODUCT_KEYS and v}
def query_filter(f,filters):
 if filters.get('qclass'):f=f[f.qclass.isin(filters['qclass'])]
 if filters.get('phrase'):
  column='query_norm' if 'query_norm' in f else 'Поисковый запрос' if 'Поисковый запрос' in f else 'query_raw'
  normalized=f[column] if column in ('query_norm','Поисковый запрос') else f[column].map(normalize)
  f=f[normalized.str.contains(normalize(filters['phrase']),regex=False,na=False)]
 kind=filters.get('unknown_kind','Все')
 if kind!='Все':
  rules=f.qrule if 'qrule' in f else f['Поисковый запрос'].map(lambda s:classify(s)[2])
  mask=rules.eq('CONFLICT_BRANDS')
  f=f[mask if kind=='Смешанные / конфликтные' else f.qclass.eq('UNKNOWN')&~mask]
 return f
def brand_shares(phrases,q):
 out=phrases.copy(deep=False)
 for suffix,w in (('A',q.attrs['a']),('B',q.attrs['b'])):
  selected=q[q.week.eq(w)&q.qclass.isin(['BRAND_PURE','BRAND_CATEGORY'])]
  for m,(stem,_) in SHARE.items():
   total=selected[m].sum();out[stem+' '+suffix+', %']=out[A.RU[m]+' '+suffix]/total*100 if total>0 else np.nan
 for m,(stem,delta) in SHARE.items():out[delta]=out[stem+' A, %']-out[stem+' B, %']
 return out
def model_structure(models):
 r=models.copy(deep=False)
 for label in ('Бренд','Небренд'):
  for s in ('A','B'):
   r['Доля '+label.lower()+'овых заказов модели '+s+', %']=r[label+' заказы '+s]/r['Заказы '+s].where(r['Заказы '+s]>0)*100
  r['Δ структуры '+label.lower()+', п.п.']=r['Доля '+label.lower()+'овых заказов модели A, %']-r['Доля '+label.lower()+'овых заказов модели B, %']
 return r
def fixed_shares(frame,denominator,a,b):
 out=frame.copy(deep=False)
 for m,(stem,delta) in SHARE.items():
  for w,s in ((a,'A'),(b,'B')):
   total=denominator.loc[denominator.week.eq(w),m].sum()
   out[stem+' '+s+', %']=out[A.RU[m]+' '+s]/total*100 if total>0 else np.nan
  out[delta]=out[stem+' A, %']-out[stem+' B, %']
 return out
def _base(revision,a,b,cohort_json,mc,mo):
 cohort=json.loads(cohort_json);s=A.bundle(a,b,cohort,mc,mo);q,e=A.rows(a,b,cohort);q.attrs.update(a=a,b=b)
 s['Брендовые запросы']=brand_shares(s['Брендовые запросы'],q)
 s['Товары и возможности']=model_structure(s['Товары и возможности'])
 return s,q,e
def ostin_bundle(a,b,filters=None,mc=100,mo=3,mode=None):
 if mode is not None:
  from .mode_views import bundle
  return bundle(a,b,filters,mc,mo,mode)
 filters=filters or {};cohort=cohort_filters(filters)
 # Product filters establish denominators. Text/class select display rows and
 # model membership; they NEVER make a single matching phrase falsely 100%.
 prepared,q,e=_base(A.pointer()['revision'],a,b,json.dumps(cohort,sort_keys=True),mc,mo);s=dict(prepared)
 for key in ('Все запросы TOP100','Брендовые запросы','Рост запросов','Падение запросов','Лучшие конверсии','Трафик слабая конверсия','Новые в TOP100','Вышли из TOP100','Бренд рост','Бренд падение'):
  s[key]=query_filter(s[key],filters)
 viewq=query_filter(q,filters)
 active=bool(filters.get('qclass') or filters.get('phrase') or filters.get('unknown_kind','Все')!='Все')
 if active:
  s['Сравнительная сводка']=fixed_shares(A.class_table(viewq,a,b),q,a,b)
  totalq=viewq[['week','qclass','row_anomaly']+A.METRICS].copy();totalq['qclass']=totalq.qclass.replace({'BRAND_PURE':'BRAND_CATEGORY'})
  total=fixed_shares(A.class_table(totalq,a,b),q,a,b);total.loc[total.qclass.eq('BRAND_CATEGORY'),'Класс']='Бренд O’STIN · выбранные фразы'
  s['Бренд и небренд']=total
  scoped=e[e.nm_id.isin(viewq.nm_id.unique())]
  s['Точки входа']=fixed_shares(A.entry_table(scoped,a,b),e,a,b)
 models=s['Товары и возможности'].copy()
 if filters.get('qclass') or filters.get('phrase') or filters.get('unknown_kind','Все')!='Все':models=models[models.nm_id.isin(viewq.nm_id.unique())]
 if filters.get('entry'):
  ep=s['Точки входа'];s['Точки входа']=ep[(ep['Раздел']+' · '+ep['Точка входа']).isin(filters['entry'])]
  chosen=e[(e.section+' · '+e.entry_point).isin(filters['entry'])]
  models=models[models.nm_id.isin(chosen.nm_id.unique())]
 s['Товары и возможности']=models
 s['Структура спроса моделей']=models[['nm_id','seller_article']+[c for c in models if c.startswith(('Доля бренд','Доля небренд','Δ структуры'))]]
 s['Диагностика R31']=pd.DataFrame([{'Показатель':'Фильтры','Значение':str(filters)},{'Показатель':'Знаменатели долей','Значение':'Полный отчёт после товарных фильтров; текст/класс/точка входа фильтруют отображение, не пересчитывают базу доли. Брендовые фразы — доля внутри полного брендового сегмента выбранных моделей.'},{'Показатель':'Модели','Значение':'Показатели модели по всем её фразам; текст/класс отбирает модели, а не подменяет структуру их спроса.'}])
 for source,rows in (('TOP100',q),('Портрет',e)):
  for w,label in ((a,'A'),(b,'B')):
   total=rows[rows.week.eq(w)][A.METRICS].sum()
   s['Диагностика R31']=pd.concat([s['Диагностика R31'],pd.DataFrame([{'Показатель':source+' '+label+' знаменатель '+m,'Значение':str(total[m])} for m in A.METRICS])],ignore_index=True)
 return s

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

def ostin_signals(s,kind='absolute',metric='orders',minimum_clicks=100,minimum_orders=3):
 f=s['Все запросы TOP100'];f=f[f['Допустим для рейтинга']&f['Переходы A'].ge(minimum_clicks)&f['Заказы A'].ge(minimum_orders)]
 delta='Δ '+A.RU[metric].lower();pct=delta+', %';_,share=SHARE[metric]
 if kind=='relative':return f[f['Заказы B'].ge(minimum_orders)].sort_values([pct,delta],ascending=False)
 if kind=='share':return f[f[share].notna()].sort_values([share,delta],ascending=False)
 if kind=='nonbrand':return f[f.qclass.eq('NON_BRAND')&f[delta].gt(0)].sort_values(delta,ascending=False)
 if kind=='brand_fall':return f[f.qclass.isin(['BRAND_PURE','BRAND_CATEGORY'])&f[delta].lt(0)].sort_values(delta)
 return f[f[delta].gt(0)].sort_values([delta,A.RU[metric]+' A'],ascending=False)

def market_compact(frame,metric='frequency'):
 out=frame[['query_raw','category']].rename(columns={'query_raw':'Фраза / источник','category':'Категория WB'}).copy()
 out['Класс запроса']=frame.qclass.map(LABELS)
 for target,source in [('Ранее',metric+'_B'),('Сейчас',metric+'_A'),('Δ ед.',metric+'_delta'),('Δ %',metric+'_pct'),('Доля ранее, %',metric+'_share_B'),('Доля сейчас, %',metric+'_share_A'),('Δ доли, п.п.',metric+'_share_pp')]:out[target]=frame[source]
 out['База изменения']=np.where(frame[metric+'_B'].eq(0)&frame[metric+'_A'].notna(),'Новый / база 0 выбранной метрики',frame.status);out['Основной предмет WB']=frame.top_subject
 return out

def cross_market(frame,context,filters=None):
 filters=filters or {};inventory=A.data()['inventory']
 find=lambda start,end:next((r['week'] for r in inventory if r['kind']=='sq' and (r['start'],r['end'])==(start,end)),None)
 wa=find(context['a']['start'],context['a']['end']);wb=find(context['b_start'],context['b_end'])
 out=frame[['query_raw','query_norm','category','frequency_B','frequency_A','frequency_delta','frequency_pct','top_subject']].copy()
 out['Частотность WB ≠ заказы O’STIN']='Разные метрики; доля рынка O’STIN не рассчитывается'
 out['Период WB A']=context['a']['start']+' — '+context['a']['end'];out['Период WB B']=context['b_start']+' — '+context['b_end']
 for s in ('A','B'):
  for m in ('orders','clicks','carts'):out['ostin_'+m+'_'+s]=np.nan
 if wa is None or wb is None:
  out['signal']='Н/Д — нет совместимых периодов O’STIN'
  last=max(r['end'] for r in inventory if r['kind']=='sq')
  out['interpretation']=f"Не сопоставляем рынок {context['a']['start']} — {context['a']['end']} с доступной историей O’STIN до {last}; это не отсутствие фразы и не нулевые продажи." if wa is None else 'Нет совместимой базы B O’STIN; рост по двум источникам не доказан.'
  return out
 q,_=A.rows(wa,wb,cohort_filters(filters));stats=q.groupby(['week','query_norm'])[A.METRICS].sum();anomalies=q.groupby(['week','query_norm']).row_anomaly.any()
 for w,s in ((wa,'A'),(wb,'B')):
  for m in A.METRICS:out['ostin_'+m+'_'+s]=out.query_norm.map(stats.xs(w,level='week')[m] if w in stats.index.get_level_values('week') else pd.Series(dtype=float))
  out['ostin_anomaly_'+s]=out.query_norm.map(anomalies.xs(w,level='week') if w in anomalies.index.get_level_values('week') else pd.Series(dtype=bool)).astype('boolean').fillna(False)
  out['ostin_orders_per_click_'+s]=out['ostin_orders_'+s]/out['ostin_clicks_'+s].where(out['ostin_clicks_'+s]>0)*100
 oa,ob=out.ostin_orders_A,out.ostin_orders_B;g=out.frequency_delta
 out['signal']=np.select([oa.isna(),ob.isna(),g.gt(0)&oa.gt(ob),g.gt(0)&oa.le(ob),g.lt(0)&oa.gt(ob)],['Не обнаружено в доступной выборке TOP-100','Н/Д — фраза отсутствует в доступной базе TOP-100 B','Частотность WB и заказы O’STIN растут','Рынок растёт — проверить использование спроса','Рынок падает, O’STIN растёт — проверить конверсию'],default='Сопоставимая фраза; отдельные метрики без причинного вывода')
 out['interpretation']='Проверить ассортимент, карточку, цену, размеры, сезонность. Нет доказанной доли рынка или влияния рекламы.'
 worse=g.gt(0)&out.ostin_orders_per_click_A.lt(out.ostin_orders_per_click_B)
 out.loc[worse,'signal']='Частотность растёт, сквозная конверсия O’STIN ухудшается — проверить предложение'
 out.loc[out.ostin_anomaly_A|out.ostin_anomaly_B,'signal']='Аномалия O’STIN A/B — сначала проверить источник, маркетинговый вывод не подтверждён'
 if 'normalization_ambiguous' in frame:
  ambiguous=frame.normalization_ambiguous
  out.loc[ambiguous,[c for c in out if c.startswith('ostin_')]]=np.nan
  out.loc[ambiguous,'signal']='Неоднозначная нормализация — автоматическое сопоставление отключено'
 return out

def assortment_context(frame,filters=None):
 q,_=A.rows(A.weeks()[-1],A.weeks()[-1],cohort_filters(filters or {}))
 dim=q[['nm_id','seller_article','product_type','gender','collection']].drop_duplicates('nm_id')
 dim['subject_norm']=dim.product_type.map(normalize)
 count=dim.groupby('subject_norm').nm_id.nunique();examples=dim.groupby('subject_norm').seller_article.agg(lambda s:'; '.join(s.head(3)))
 out=frame.copy();key=out.top_subject.map(normalize)
 out['Исторических моделей O’STIN по предмету']=key.map(count)
 out['Примеры артикулов (история, не текущие остатки)']=key.map(examples)
 period=next(r for r in A.data()['inventory'] if r['kind']=='sq' and r['week']==A.weeks()[-1])
 out['Дата ассортимента Search']=period['start']+' — '+period['end']+'; не подтверждает наличие/остаток сейчас'
 return out

_base.cache_clear=lambda:None
