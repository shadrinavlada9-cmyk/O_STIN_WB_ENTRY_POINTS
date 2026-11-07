"""R3 table coverage restored over unchanged R3.1 data and classifier."""
import json,hashlib,time
import numpy as np
import pandas as pd
import streamlit as st
from . import analytics as A
from . import r31_views as V
from . import page_r31 as P
from . import market as M
from .classifier import LABELS,normalize
from .r32_tables import number,excel
from public_search.downloads import table
from .export_ticket import Sheets

MODES=['Точки входа','Брендовые запросы','Все запросы TOP-100','Какие товары требуют внимания','Обрати внимание']
def filters():
 opts=P.facets(A.pointer()['revision']);f={}
 with st.expander('Фильтры O’STIN — фраза, класс и товары'):
  c=st.columns(3)
  for i,(key,name) in enumerate([('gender','Гендер'),('collection','Коллекция'),('product_type','Тип товара')]):
   with c[i]:f[key]=st.multiselect(name,opts[key],key='r32_filter_'+key)
  for i,(key,name) in enumerate([('seller_article','Артикул продавца (точно)'),('nm_id','nmID WB (точно)'),('cm','ЦМ (подтверждённая связь)')]):
   with c[i]:f[key]=[v.strip() for v in st.text_input(name,key='r32_filter_'+key).split(',') if v.strip()]
  with c[0]:f['qclass']=st.multiselect('Класс запроса',list(LABELS),format_func=LABELS.get,key='r32_filter_class')
  with c[1]:f['phrase']=st.text_input('Поиск по фразе',key='r32_filter_phrase')
  with c[2]:f['unknown_kind']=st.selectbox('Подтип неоднозначных запросов',['Все','Смешанные / конфликтные','Прочие неопределённые'],key='r32_filter_unknown')
  f['entry']=st.multiselect('Точка входа O’STIN',opts['entry'],key='r32_filter_entry')
 return f
def heading(text):st.markdown('### '+text)
def put(s,name,f,key,sort=None,height=400):
 s[name]=f;heading(name);table(f,key,sort,height,default_ascending='снижение' in name.lower())
def compact(f,m='orders',ids=None):
 out=V.compact(f,m,ids)
 if ids is None and 'Исходные фразы' in f:out['Фраза / источник']=f['Исходные фразы']
 if 'Флаги A / B' in out:
  out['Аномалии сейчас / ранее']=out.pop('Флаги A / B').map({'A+B':'Обе недели','A':'Сейчас','B':'Ранее','':'Нет'})
 return out
def no_shares(f):
 out=f[[c for c in f if 'share' not in c.lower() and not any(w in c.lower() for w in ('доля','доли'))]].copy()
 if {'Ранее','Сейчас','База изменения'}<=set(out):
  out.loc[out['Ранее'].isna()&out['Сейчас'].notna(),'База изменения']='Н/Д — нет подтверждённой базы ранее'
  out.loc[out['Сейчас'].isna(),'База изменения']='Н/Д — нет строки в доступной выборке сейчас'
 if {'status','frequency_B','frequency_A'}<=set(out):
  out.loc[out.frequency_B.isna()&out.frequency_A.notna(),'status']='Н/Д — нет подтверждённой базы ранее'
 return out
def conversions(f,ids=None):
 out=compact(f,'orders',ids)
 first=['Фраза / источник'];cols=[c for c in f if any(x in c for x in (' / ','CTR '))]
 return pd.concat([out[first],f[cols]],axis=1)
def metrics(s,f,prefix,key,ids=None):
 s[prefix+' полные поля']=f
 for m,name in [('orders','Заказы'),('clicks','Переходы'),('carts','Корзины')]:
  put(s,prefix+' — '+name,compact(f,m,ids),key+'_'+m,'Δ ед.')
 put(s,prefix+' — Конверсии',conversions(f,ids),key+'_cr',height=300)
 if 'Показы A' in f:
  out=compact(f,ids=ids)[['Фраза / источник']]
  for c in ['Показы B','Показы A','Δ показы','Δ показы, %']:out[c]=f[c]
  put(s,prefix+' — Показы',out,key+'_impressions',height=300)
def detail_rows(a,b,filters,*,query=None,entry=None,nm=None,sources=('sq','ep')):
 current=A.SD.CURRENT.get();selection=V.cohort_filters(filters)
 if nm is not None:selection['nm_id']=[str(nm)]
 selection['_sources']=sources
 if query is not None and current is not None:
  f=current['sq'];selection['_raw_queries']=f.loc[f.query_norm.eq(query),'query_raw'].drop_duplicates().tolist()
 if entry is not None and current is not None:
  f=current['ep'];chosen=f[(f.section+' · '+f.entry_point).eq(entry)]
  selection['_entry_pairs']=chosen[['section','entry_point']].drop_duplicates().values.tolist()
 if sources==('ep',) and (any(filters.get(k) for k in ('qclass','phrase')) or filters.get('unknown_kind','Все')!='Все'):selection['_sources']=('sq','ep')
 with A.SD.request(A.ROOT,A.pointer()['revision'],a,b,selection,'DETAIL_SOURCE'):
  q,e=A.rows(a,b,V.cohort_filters(selection))
 q=V.query_filter(q,filters)
 if filters.get('qclass') or filters.get('phrase') or filters.get('unknown_kind','Все')!='Все':e=e[e.nm_id.isin(q.nm_id.unique())]
 if filters.get('entry'):
  e=e[(e.section+' · '+e.entry_point).isin(filters['entry'])];q=q[q.nm_id.isin(e.nm_id.unique())]
 return q,e
def download(s,key):
 from .export_ticket import prepare
 ticket=prepare(s)
 from public_search.excel_controls import control
 control(ticket,'r32_excel_'+key,'WB_SEARCH_R32_'+key+'.xlsx','все таблицы выбранного режима')
def brand_subclasses(s,a,b,filters):
 q,_=A.rows(a,b,V.cohort_filters(filters));q.attrs.update(a=a,b=b)
 cl=s['Сравнительная сводка'];cl=cl[cl.qclass.isin(['BRAND_PURE','BRAND_CATEGORY'])].copy()
 return V.brand_shares(cl,q)
def direction(f,positive=True):
 col='Δ заказы'
 return f[f[col].gt(0) if positive else f[col].lt(0)].sort_values(col,ascending=not positive)
def ostin(mode,filters):
 a,b=P.week_controls()
 with A.request(a,b,filters,mode):return _ostin(mode,filters,a,b)

def _ostin(mode,filters,a,b):
 st.session_state['public_export_context']={'Источник':'O’STIN TOP100 и Портрет: раздельные отчёты','Неделя сейчас':a,'Неделя ранее':b,'Режим':mode,'Фильтры':filters}
 mc,mo=100,3
 with st.expander('Порог достаточности диагностических рейтингов'):
  c1,c2=st.columns(2)
  with c1:mc=st.number_input('Минимум переходов',1,value=100,key='r32_mc')
  with c2:mo=st.number_input('Минимум заказов',1,value=3,key='r32_mo')
  st.caption('Это нижняя граница оценки, не доказательство устойчивости результата. Аномалии не используются для рекомендаций.')
 with st.spinner('Подготовленные данные O’STIN…'):base=P.calc(A.pointer()['revision'],a,b,json.dumps(filters,sort_keys=True),mc,mo,mode)
 if mode==MODES[3]:
  # Product results are already aggregated; selected-product detail below
  # starts its own nm-filtered request. Release raw two-week frames before
  # building presentation/export views. No values or formulas are changed.
  current=A.SD.CURRENT.get()
  if current is not None:current.clear()
  A.SD.release()
 s=Sheets();st.caption('Только товары O’STIN. TOP-100 и Портрет — независимые отчёты. Доли относятся к полному сопоставимому отчёту выбранных товаров; текст и класс не меняют знаменатель.')
 if mode==MODES[0]:
  f=base['Точки входа']
  search=f[f['Точка входа'].eq('Поисковая выдача')];recommend=f[f['Раздел'].eq('Карточка товара')&f['Точка входа'].eq('Подборки и рекомендации')]
  if len(search) and len(recommend):
   x,y=search.iloc[0],recommend.iloc[0]
   st.write('**Что изменилось:** поисковая выдача: '+number(x['Δ заказы'],'Δ')+' заказов, '+number(x['Δ доли, п.п.'],'Δ п.п.')+' доли; рекомендации в карточке: '+number(y['Δ заказы'],'Δ')+' заказов, '+number(y['Δ доли, п.п.'],'Δ п.п.')+' доли. Изменение объёма и вклада — разные сигналы; это не доказанная причина.')
  metrics(s,f,'Общая динамика точек входа','r32_ep',['Раздел','Точка входа'])
  st.caption('Конверсии пересчитаны по суммам числителей и знаменателей. Исходные аномальные значения сохранены. Родительские и дочерние уровни не суммируются повторно.')
  put(s,'Какие точки входа дали рост заказов',compact(direction(f),ids=['Раздел','Точка входа']),'r32_ep_up','Δ ед.',300)
  put(s,'Какие точки входа дали снижение заказов',compact(direction(f,False),ids=['Раздел','Точка входа']),'r32_ep_down','Δ ед.',300)
  heading('Точка входа → товары')
  if len(f):
   selected=st.selectbox('Выберите точку входа',f['Раздел']+' · '+f['Точка входа'],key='r32_ep_selected')
   _,e=detail_rows(a,b,filters,entry=selected,sources=('ep',));e=e[(e.section+' · '+e.entry_point).eq(selected)]
   d=A.entry_table(e,a,b,['seller_article','nm_id']);metrics(s,d,'Товары выбранной точки входа','r32_ep_products',['seller_article','nm_id'])
 elif mode==MODES[1]:
  cl=base['Бренд и небренд'];brand=cl[cl.qclass.eq('BRAND_CATEGORY')];nonbrand=cl[cl.qclass.eq('NON_BRAND')]
  if len(brand):
   v=brand.iloc[0];msg='Брендовые заказы: '+number(v['Заказы B'])+' → '+number(v['Заказы A'])+'; '+number(v['Δ заказы, %'],'Δ %')+', доля '+number(v['Δ доли, п.п.'],'Δ п.п.')+'.'
   if len(nonbrand):msg+=' Небрендовые заказы: '+number(nonbrand.iloc[0]['Δ заказы, %'],'Δ %')+'.'
   st.write('**Что изменилось:** '+msg+' Это динамика TOP-100, а не самостоятельная оценка узнаваемости или причин роста.')
  put(s,'Структура поискового спроса — четыре класса',compact(cl),'r32_classes','Сейчас',240)
  sub=brand_subclasses(base,a,b,filters)
  st.caption('Чистый бренд и бренд + категория: знаменатель долей — только брендовый сегмент выбранных товаров, не весь TOP-100.')
  put(s,'Чистый бренд / бренд + категория',compact(sub),'r32_brand_sub','Сейчас',220)
  f=base['Брендовые запросы']
  s['Полные брендовые фразы']=f
  put(s,'Брендовые запросы — лидеры по заказам',compact(f),'r32_brand_leads','Сейчас')
  put(s,'Брендовые запросы — максимальный прирост',compact(direction(f)),'r32_brand_up','Δ ед.',300)
  put(s,'Брендовые запросы — максимальное снижение',compact(direction(f,False)),'r32_brand_down','Δ ед.',300)
  st.caption('Таблицы показывают фактические изменения, в том числе флаги аномалий A/B. Они не являются автоматической рекомендацией. Нет строки в TOP-100 — не подтверждённый ноль.')
  phrase_detail(s,f,a,b,filters,'brand')
 elif mode==MODES[2]:
  grain=st.radio('Зерно таблицы',['Нормализованная фраза','Модель × фраза'],horizontal=True,key='r32_grain');ids=None
  f=base['Все запросы TOP100']
  if grain=='Модель × фраза':
   q,_=detail_rows(a,b,filters,sources=('sq',));denominator,_=A.rows(a,b,V.cohort_filters(filters))
   f=V.fixed_shares(A.compare(q,a,b,['query_norm','seller_article','nm_id','cm','product_type','gender','collection','qclass']),denominator,a,b).rename(columns={'query_norm':'Поисковый запрос'})
   ids=['Поисковый запрос','seller_article','nm_id']
  metrics(s,f,'Все запросы TOP-100','r32_all',ids)
  put(s,'Запросы — рост заказов',compact(direction(f),ids=ids),'r32_all_up','Δ ед.',300)
  put(s,'Запросы — снижение заказов',compact(direction(f,False),ids=ids),'r32_all_down','Δ ед.',300)
  view=st.selectbox('Диагностическая выборка',['Лучшие конверсии','Трафик слабая конверсия','Новые в TOP100','Вышли из TOP100'],key='r32_diag')
  put(s,view,base[view],'r32_selection','Заказы A')
  phrase_detail(s,f,a,b,filters,'all')
 elif mode==MODES[3]:
  f=base['Товары и возможности'];heading('Что проверить маркетологу — факты и конкретные товары')
  wanted=['seller_article','nm_id','Возможность','Факт и сравнение','Проверка','Ограничение группы','Аномалия A','Аномалия базы B']
  top=base['Топ возможностей'];top=top[top.nm_id.isin(f.nm_id)]
  put(s,'Приоритетные товары для проверки',top[[c for c in wanted if c in top]],'r32_top_actions')
  actions=f[[c for c in wanted if c in f]];put(s,'Товары — что требует проверки',actions,'r32_actions')
  attributes=['seller_article','nm_id','cm','gender','collection','product_type','Основные фразы A','Главная точка входа A']
  put(s,'Характеристики товаров и основные запросы',f[[c for c in attributes if c in f]],'r32_attributes')
  brand_fields=['seller_article','nm_id']+[c for c in f if c.startswith(('Бренд заказы','Небренд заказы','Бренд переходы','Небренд переходы'))]
  put(s,'Брендовые и небрендовые показатели товаров',f[brand_fields],'r32_model_brands')
  metrics(s,f,'Показатели товаров','r32_models',['seller_article','nm_id'])
  put(s,'Структура брендового спроса внутри товара',base['Структура спроса моделей'],'r32_structure')
  if len(f):
   nm=st.selectbox('Разбор выбранной модели',f.nm_id.tolist(),format_func=lambda n:str(n)+' · '+str(f.loc[f.nm_id.eq(n),'seller_article'].iloc[0]),key='r32_model')
   model=f[f.nm_id.eq(nm)].iloc[0];st.write('**Факт:** '+str(model['Факт и сравнение']));st.write('**Что проверить:** '+str(model['Проверка']))
   q,e=detail_rows(a,b,filters,nm=nm);q=q[q.nm_id.eq(nm)];e=e[e.nm_id.eq(nm)]
   metrics(s,A.phrases(q,a,b),'Фразы выбранной модели','r32_model_q')
   metrics(s,A.entry_table(e,a,b),'Точки входа выбранной модели','r32_model_e',['Раздел','Точка входа'])
  st.caption('Модель WB включает все размеры. Это гипотезы для проверки карточек и ассортимента, не автоматическое поручение увеличить бюджет.')
 # Full R3 metrics stay in the workbook even when UI tables are split logically.
 for key in ('Источники','Диагностика R31','Влияние аномалий','Аномалии строк','Аномалии показов'):
  if key in base:s[key]=base[key]
 with st.expander('Аномалии и ограничения — исходные значения не исправлялись'):
  st.warning('Аномалии A и базы B включены в фактические итоги, но исключаются из рекомендательных рейтингов. Значения выше 100% не обрезаются.')
  for key in ('Влияние аномалий','Аномалии показов','Диагностика R31'):
   if key in base:heading(key);table(base[key],'r32_'+key,height=250)
 download(s,mode+'_'+a+'_'+b)
 st.caption(A.pointer()['missing_next']+'; это не нулевая статистика и не блокирует финансовые обновления.')
def phrase_detail(s,f,a,b,filters,key):
 heading('Конкретная фраза → товары O’STIN')
 if f.empty:return
 selected=st.selectbox('Выберите фразу',f['Поисковый запрос'].drop_duplicates().tolist(),key='r32_phrase_'+key)
 q,_=detail_rows(a,b,filters,query=normalize(selected),sources=('sq',));q=q[q.query_norm.eq(normalize(selected))]
 d=A.compare(q,a,b,['seller_article','nm_id','cm','product_type','gender','collection'])
 metrics(s,d,'Товары выбранной фразы','r32_detail_'+key,['seller_article','nm_id'])
def market_context():
 store=M.Store();snapshots=store.snapshots()
 if not snapshots:st.info('Нет принятого рыночного отчёта.');return None
 category=st.selectbox('Категория WB',sorted({m['category'] for m in snapshots}),key='r32_category')
 mode=st.selectbox('Сравнение рыночных снимков',['Последние два отчёта','Начало месяца → последний снимок','Произвольные два снимка'],key='r32_compare')
 own=store.snapshots(category);ids=[m['id'] for m in own];labels={m['id']:m['received_at'][:10]+' · '+m['start']+' — '+m['end']+' · '+m['sha256'][:8] for m in own};kw={}
 if mode=='Начало месяца → последний снимок':
  months=sorted({M.received_month(m) for m in own});month=st.selectbox('Месяц загрузки',months,index=len(months)-1)
  elig=[m['id'] for m in own if M.received_month(m)==month];default=store.index()['monthly_baselines'][category+'|'+month]
  kw={'month':month,'base':st.selectbox('Базовый снимок месяца',elig,index=elig.index(default),format_func=labels.get)}
  st.caption('Изменение недельного уровня спроса между снимками, не накопленный спрос за месяц.')
 elif mode=='Произвольные два снимка':
  c1,c2=st.columns(2)
  with c1:kw['current']=st.selectbox('Сейчас',ids,index=len(ids)-1,format_func=labels.get)
  with c2:kw['base']=st.selectbox('Ранее',ids,index=max(0,len(ids)-2),format_func=labels.get)
 ctx=M.choose(store,category,mode,**kw)
 receipt=M.dt.datetime.fromisoformat(ctx['a']['received_at']).astimezone(M.ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M МСК')
 st.caption('Рынок WB / '+category+' · ранее '+str(ctx['b_start'])+' — '+str(ctx['b_end'])+' → сейчас '+ctx['a']['start']+' — '+ctx['a']['end']+'. Получен '+receipt+'; это не дата статистики одного дня.')
 for warning in ctx['warnings']:
  if 'Первый снимок' in warning:warning='Первый снимок: база встроена в текущий файл и относится только к представленным в нём фразам. Это не полная предыдущая выборка; вышедшие фразы неизвестны.'
  st.warning(warning)
 return store,ctx
def market_view(all_rows=False):
 heading('Обрати внимание — общий поисковый спрос Wildberries')
 st.caption('Категория WB целиком. Показатели не являются заказами или выручкой товаров O’STIN. Доли не рассчитываются в этом режиме.')
 context=market_context()
 if context is None:return
 store,ctx=context
 st.session_state['public_export_context']={'Источник':'Рынок WB, не заказы O’STIN','Категория':ctx['category'],'Период сейчас':[ctx['a']['start'],ctx['a']['end']],'Период ранее':[ctx['b_start'],ctx['b_end']],'Ограничения':ctx['warnings']}
 c1,c2=st.columns([3,2])
 with c1:phrase=st.text_input('Поиск по фразе',key='r32_market_phrase')
 with c2:classes=st.multiselect('Класс запроса',list(LABELS),format_func=LABELS.get,key='r32_market_class')
 st.session_state['public_export_context'].update({'Поиск по фразе':phrase,'Классы':classes})
 seal=hashlib.sha256((store.root/'index.json').read_bytes()).hexdigest();full=P.market_calc(seal,json.dumps(ctx,sort_keys=True))
 f=M.filter_rows(full,phrase,classes)
 from public_search.raw_export import MarketRaw
 raw_request=MarketRaw(str(store.root),json.dumps(ctx),phrase,tuple(classes))
 st.download_button('⬇ CSV/ZIP — все рыночные фразы по фильтрам, без порога рейтинга',data=raw_request.build,file_name='WB_FULL_FILTERED.zip',key='market_raw_full',on_click='ignore')
 kinds={k:v for k,v in P.MARKET_KINDS.items() if v!='share'}
 kind='all' if all_rows else kinds[st.selectbox('Рыночная выборка',list(kinds),key='r32_market_kind')]
 with st.expander('Пороги значимости'):
  c=st.columns(4)
  with c[0]:mf=st.number_input('Минимальная частотность',0,value=1000)
  with c[1]:mb=st.number_input('Минимальная база',0,value=100)
  with c[2]:ma=st.number_input('Минимальный прирост',0,value=100)
  with c[3]:mp=st.number_input('Минимальный прирост, %',0.0,value=10.0)
 selected=f if kind=='all' or ctx['single'] and kind!='exited' else M.rank(f,kind,mf,mb,ma,mp)
 if len(selected):
  row=selected.iloc[0];st.write('**Что изменилось:** '+str(row.query_raw)+' — '+number(row.frequency_B)+' → '+number(row.frequency_A)+' запросов; '+number(row.frequency_delta,'Δ')+' / '+number(row.frequency_pct,'Δ %')+'. Проверить сезонность и релевантный ассортимент. Рост частотности не является поручением увеличить рекламный бюджет.')
 if kind=='exited' and not ctx['full_structure']:st.info('Для вышедших из выборки фраз нужен второй полный снимок.')
 del full,f
 s=Sheets();view=no_shares(V.market_compact(selected));s['Растущий спрос WB — частотность']=view
 heading('Рыночные запросы — частотность')
 sort='Сейчас' if ctx['single'] or kind in ('stable','new','all') else 'Ранее' if kind=='exited' else 'Δ %' if kind=='relative' else 'Δ ед.'
 table(view,'r32_market_'+kind,sort,default_ascending=kind=='fall')
 for metric,title in [('clicks','Переходы в карточки'),('carts','Корзины'),('orders','Заказали по отчёту WB')]:
  put(s,title,no_shares(V.market_compact(selected,metric)),'r32_market_'+metric,'Δ ед.')
 st.caption('Поле WB «Заказали товаров» описано в отчёте как число покупателей, купивших после поиска. Это метрика отчёта, не финансовые заказы O’STIN. Нет разделения платного и органического спроса.')
 s['Полные показатели рынка']=no_shares(selected)
 s['Снимки и периоды']=pd.DataFrame([{k:v for k,v in m.items() if k in ('id','category','start','end','previous_start','previous_end','received_at','original_file','sha256','rows','schema_version','classifier_version')} for m in store.snapshots(ctx['category'])])
 s['Ограничения']=pd.DataFrame({'Предупреждение':[w for w in ctx['warnings'] if 'дол' not in w.lower()]+['WB целиком, не заказы O’STIN. Встроенная база не является полной прошлой выборкой. Отсутствие фразы не доказывает нулевой спрос.']})
 with st.expander('Исходные конверсии, аномалии и история снимков'):
  cols=['query_raw']+[c for c in selected if 'cr_' in c or 'anomaly' in c]
  table(selected[cols],'r32_market_cr');table(s.dataframe('Снимки и периоды'),'r32_market_meta')
 download(s,'MARKET_'+ctx['a']['sha256'][:8])
def render(context=None):
 st.header('Поиск и точки входа · R3.2')
 mode=st.segmented_control('Режим',MODES,default=MODES[0],key='r32_mode') or MODES[0]
 if mode==MODES[4]:
  with A.SD.GATE:
   try:market_view()
   finally:A.SD.release()
 else:ostin(mode,filters())
