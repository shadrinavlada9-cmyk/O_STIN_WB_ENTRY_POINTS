"""Five-mode Search R3.1 UI; existing R3 data/calculations, separate WB market."""
import json,hashlib,time
import numpy as np
import pandas as pd
import streamlit as st
from . import analytics as A
from . import r31_views as V
from . import market as M
from .classifier import LABELS,normalize

MODES=['Точки входа','Брендовые запросы','Все запросы TOP-100','Товары и возможности','Обрати внимание']
KINDS={'Сильнейший абсолютный рост':'absolute','Сильнейший относительный рост':'relative','Наибольший прирост доли':'share','Растущий небрендовый спрос':'nonbrand','Падение важных брендовых фраз':'brand_fall'}
MARKET_KINDS={'Сильнейший абсолютный рост':'absolute','Сильнейший относительный рост при достаточной базе':'relative','Наибольший прирост доли':'share','Падение спроса':'fall','Новые в доступной выборке':'new','Вышедшие из доступной выборки':'exited','Крупный стабильный спрос':'stable','Все фразы отчёта':'all'}

def calc(revision,a,b,filters,mc,mo,mode=None):return V.ostin_bundle(a,b,json.loads(filters),mc,mo,mode)
from .metadata_cache import Cache
_facets=Cache()
def facets(revision):return _facets.get((str(A.ROOT),revision),A.options)
facets.clear=_facets.clear
def market_calc(index_seal,context_json):return M.comparison(M.Store(),json.loads(context_json))

def fmt(v,dec=0):return 'Н/Д' if pd.isna(v) else f'{v:,.{dec}f}'.replace(',',' ').replace('.',',')
def table(frame,key,sort='Δ ед.',height=440):
 if frame.empty:st.info('Нет данных по выбранным условиям. Это не нулевой спрос.');return
 c1,c2,c3=st.columns([3,1,1]);nums=[c for c in frame if pd.api.types.is_numeric_dtype(frame[c]) and not pd.api.types.is_bool_dtype(frame[c])]
 with c1:chosen=st.selectbox('Сортировка',nums,index=nums.index(sort) if sort in nums else 0,key=key+'_sort') if nums else None
 with c2:ascending=st.checkbox('По возрастанию',key=key+'_asc')
 maximum=max(1,(len(frame)+99)//100)
 if st.session_state.get(key+'_page',1)>maximum:st.session_state[key+'_page']=1
 with c3:page=st.number_input('Страница',min_value=1,max_value=maximum,value=1,key=key+'_page')
 rows=frame.sort_values(chosen,ascending=ascending,na_position='last') if chosen else frame
 shown=A.display(rows.iloc[(page-1)*100:page*100]);configs={}
 if len(shown.columns):configs[shown.columns[0]]=st.column_config.TextColumn(width='large',pinned=True)
 for c in nums:
  name=A.DISPLAY_NAMES.get(c,c);dec=2 if '%' in c or 'п.п.' in c else 0
  if shown[name].isna().any():
   # Display only: retain numerical/null values in calculations and Excel.
   shown[name]=shown[name].map(lambda v:fmt(v,dec))
   configs[name]=st.column_config.TextColumn()
  else:configs[name]=st.column_config.NumberColumn(format='%.2f' if dec else '%d')
 st.caption(f'{len(frame):,} строк · показано до 100 · Excel содержит весь выбранный срез'.replace(',',' '))
 st.dataframe(shown,column_config=configs,hide_index=True,width='stretch',height=height)

def shared_filters():
 opts=facets(A.pointer()['revision']);f={}
 with st.expander('Товарные фильтры O’STIN',expanded=False):
  cols=st.columns(3)
  for col,(key,label) in zip(cols,[('gender','Гендер O’STIN'),('collection','Коллекция'),('product_type','Тип товара')]):
   with col:f[key]=st.multiselect(label,opts[key],key='r31_'+key)
  for col,(key,label) in zip(cols,[('seller_article','Артикул продавца (точно)'),('nm_id','nmID WB (точно)'),('cm','ЦМ (подтверждённая связь)')]):
   with col:f[key]=[v.strip() for v in st.text_input(label,key='r31_'+key).split(',') if v.strip()]
  f['entry']=st.multiselect('Точка входа O’STIN',opts['entry'],key='r31_entry')
  st.caption('Товарные фильтры определяют знаменатель O’STIN. В рыночном отчёте они используются только для исторического ассортиментного контекста, не изменяют показатели WB.')
 c1,c2,c3=st.columns([2,3,2])
 with c1:f['qclass']=st.multiselect('Класс запроса',list(LABELS),format_func=LABELS.get,key='r31_class')
 with c2:f['phrase']=st.text_input('Поиск по фразе',key='r31_phrase')
 with c3:f['unknown_kind']=st.selectbox('Подтип неоднозначных запросов',['Все','Смешанные / конфликтные','Прочие неопределённые'],key='r31_unknown')
 return f

def week_label(w):
 r=next(r for r in A.data()['inventory'] if r['kind']=='sq' and r['week']==w)
 return f"{w} · {r['start']} — {r['end']}"
def week_controls():
 ws=A.weeks();c1,c2=st.columns(2)
 with c1:a=st.selectbox('Неделя O’STIN сейчас',ws,index=len(ws)-1,format_func=week_label,key='r31_a')
 with c2:b=st.selectbox('Неделя O’STIN ранее',ws,index=max(0,len(ws)-2),format_func=week_label,key='r31_b')
 st.caption('O’STIN: '+week_label(b)+' → '+week_label(a)+'. Обе недели из собственных файлов; 7 календарных дней в каждой.')
 return a,b
def metric_select(market=False):
 choices={'Заказы':'orders','Переходы':'clicks','Корзины':'carts'}
 if market:choices={'Частотность WB':'frequency','Переходы в карточки WB':'clicks','Корзины WB':'carts','Заказали по отчёту WB':'orders'}
 label=st.radio('Анализируемая метрика',list(choices),horizontal=True,key='r31_market_metric' if market else 'r31_metric')
 return choices[label]
def download(sheets,key):
 st.download_button('⬇ Excel — текущие фильтры, сравнения и предупреждения',data=lambda:A.excel(sheets() if callable(sheets) else sheets),file_name='WB_SEARCH_R31_'+key+'.xlsx',mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',key='r31_excel_'+key)

def ostin(mode,filters):
 a,b=week_controls();metric=metric_select();mc,mo=100,3
 with st.expander('Достаточность статистики O’STIN',expanded=False):
  c1,c2=st.columns(2)
  with c1:mc=st.number_input('Минимум переходов O’STIN A',min_value=1,value=100,key='r31_mc')
  with c2:mo=st.number_input('Минимум заказов O’STIN A',min_value=1,value=3,key='r31_mo')
  st.caption('Нижняя граница диагностики, не доказательство устойчивого тренда. Относительные лидеры дополнительно требуют ненулевую достаточную базу заказов.')
 start=time.perf_counter()
 with st.spinner('Сравнение O’STIN из подготовленных данных…'):s=calc(A.pointer()['revision'],a,b,json.dumps(filters,sort_keys=True),mc,mo)
 s=dict(s);st.caption('Знаменатель долей: полный отчёт выбранных товаров за каждую неделю. Поиск по тексту/классу фильтрует строки, не делает одну найденную фразу 100% всей выборки.')
 if mode=='Точки входа':
  f=s['Точки входа'];view=V.compact(f,metric,['Раздел','Точка входа']);s['Экран точки входа']=view
  if len(f):
   lead=f.sort_values('Δ '+A.RU[metric].lower(),ascending=False,na_position='last').iloc[0]
   st.write(f"**Что изменилось:** {lead['Раздел']} · {lead['Точка входа']}: {fmt(lead[A.RU[metric]+' B'])} → {fmt(lead[A.RU[metric]+' A'])}; Δ {fmt(lead['Δ '+A.RU[metric].lower()])}. Это фактическая динамика отчёта, не доказанная причина.")
  table(view,'r31_ep')
  st.caption('Доли — внутри полного Портрета выбранных товаров. Фильтр фразы/класса отбирает модели, встречавшиеся в TOP-100 с этими фразами; это НЕ прямая атрибуция запросов точкам входа. TOP-100 к Портрету не прибавляется. Аномалии A/B сохранены.')
  with st.expander('Точка входа → товары и конверсии'):
   if len(f):
    names=f['Раздел']+' · '+f['Точка входа'];selected=st.selectbox('Детализация точки входа',names,key='r31_ep_detail')
    _,e=A.rows(a,b,V.cohort_filters(filters));e=e[(e.section+' · '+e.entry_point).eq(selected)]
    detail=A.entry_table(e,a,b,['nm_id','seller_article']);s['Товары точки входа']=detail
    table(V.compact(detail,metric,['seller_article','nm_id']),'r31_ep_models')
 elif mode=='Брендовые запросы':
  summary=s['Бренд и небренд'];view=V.compact(summary,metric);s['Экран классы']=view
  whole=summary[summary.qclass.eq('BRAND_CATEGORY')]
  if len(whole):
   v=V.compact(whole,metric).iloc[0];st.write(f"**Что изменилось:** бренд O’STIN — {fmt(v['Ранее'])} → {fmt(v['Сейчас'])}; доля {fmt(v['Доля ранее, %'],2)}% → {fmt(v['Доля сейчас, %'],2)}% ({fmt(v['Δ доли, п.п.'],2)} п.п.). Рост доли не является самостоятельным измерением узнаваемости.")
  table(view,'r31_classes',height=230)
  st.markdown('**Чистый бренд / бренд + категория**');table(V.compact(s['Сравнительная сводка'],metric),'r31_subclasses',height=235)
  st.markdown('**Конкретные брендовые фразы — доля внутри брендового сегмента**')
  brand=V.compact(s['Брендовые запросы'],metric);s['Экран брендовые фразы']=brand;table(brand,'r31_brand')
 elif mode=='Все запросы TOP-100':
  view=V.compact(s['Все запросы TOP100'],metric);s['Экран TOP100']=view
  table(view,'r31_top100')
  st.caption('Доля фразы — в полном TOP-100 выбранных товаров. Вышел из доступной выборки ≠ нулевой спрос. Частотность WB в TOP-100 не суммируется по товарам.')
  with st.expander('Конкретная фраза → артикулы / ЦМ'):
   exact=st.text_input('Фраза для товарной детализации',key='r31_exact')
   if exact:
    q,_=A.rows(a,b,V.cohort_filters(filters));q=q[q.query_norm.eq(normalize(exact))]
    detail=A.compare(q,a,b,['nm_id','seller_article','cm']);s['Модели конкретной фразы']=detail
    table(V.compact(detail,metric,['seller_article','nm_id','cm']),'r31_phrase_models')
 elif mode=='Товары и возможности':
  models=s['Товары и возможности'];view=V.compact(models,metric,['seller_article','nm_id']);s['Экран модели']=view
  st.caption('Основная таблица: доля модели в TOP-100 товарной выборки. Текст/класс отбирает модели, но показатели модели остаются по всем её фразам.')
  table(view,'r31_models')
  st.markdown('**Структура спроса внутри каждой модели — отдельный знаменатель**')
  structure=s['Структура спроса моделей'];table(structure,'r31_structure',sort=next((c for c in structure if c.startswith('Δ структуры')),None),height=320)
  with st.expander('Возможности и фактическое обоснование'):
   fields=['seller_article','Возможность','Факт и сравнение','Проверка','Ограничение группы']
   table(models[fields],'r31_actions',height=350)
 elif mode=='Обрати внимание':
  label=st.selectbox('Сигналы O’STIN',list(KINDS),key='r31_ostin_signal');kind=KINDS[label]
  f=V.ostin_signals(s,kind,metric,mc,mo);view=V.compact(f,metric);s['Растущие запросы O’STIN']=f;s['Экран сигналы O’STIN']=view
  st.markdown('**Обрати внимание — изменения O’STIN**');table(view,'r31_ostin_attention')
  if len(f):
   v=f.iloc[0];st.write(f"**Факт → проверка:** {v['Исходные фразы']}: {fmt(v[A.RU[metric]+' B'])} → {fmt(v[A.RU[metric]+' A'])}. Проверить карточки, ассортимент и сезонность; рекламная причинность не доказана.")
 with st.expander('Конверсии, аномалии и полная диагностика R3',expanded=False):
  st.warning('Исходные значения >100% не обрезаются. Итоги сохраняют аномальные строки; рейтинги O’STIN исключают аномалии A/B. Конверсия WB=заказы/корзины, сквозная=заказы/переходы.')
  table(s['Влияние аномалий'],'r31_anomaly_impact',sort='orders',height=230)
  st.dataframe(s['Диагностика R31'],hide_index=True,width='stretch')
  table(s['Аномалии строк'],'r31_anomalies',sort='orders',height=300)
 download(s,a+'_'+b)
 st.caption(A.pointer()['missing_next']+' · финансовый MAIN этим не блокируется. '+f'Расчёт/кеш R3.1: {time.perf_counter()-start:.2f} с')

def market_context(store):
 snapshots=store.snapshots()
 if not snapshots:st.info('Рыночные снимки пока не приняты. Четыре режима O’STIN доступны.');return None
 categories=sorted({r['category'] for r in snapshots});c1,c2=st.columns([1,2])
 with c1:category=st.selectbox('Категория WB',categories,key='r31_category')
 with c2:mode=st.selectbox('Сравнение рыночных снимков',['Последние два отчёта','Начало месяца → последний снимок','Произвольные два снимка'],key='r31_snapshot_mode')
 own=store.snapshots(category);label=lambda m:f"{m['received_at'][:10]} · данные {m['start']} — {m['end']} · {m['sha256'][:8]}"
 opts={m['id']:label(m) for m in own};kwargs={}
 if mode=='Начало месяца → последний снимок':
  months=sorted({M.received_month(r) for r in own});month=st.selectbox('Месяц загрузки снимков (Москва)',months,index=len(months)-1,key='r31_market_month')
  eligible=[r['id'] for r in own if M.received_month(r)==month];default=store.index()['monthly_baselines'][category+'|'+month]
  base=st.selectbox('Базовый снимок месяца (можно изменить)',eligible,index=eligible.index(default),format_func=opts.get,key='r31_month_base')
  kwargs={'month':month,'base':base};st.caption('Изменение уровня спроса между окнами отчётов, НЕ накопленный спрос за месяц.')
 elif mode=='Произвольные два снимка':
  ids=list(opts);c1,c2=st.columns(2)
  with c1:a=st.selectbox('Снимок сейчас',ids,index=len(ids)-1,format_func=opts.get,key='r31_custom_a')
  with c2:b=st.selectbox('Снимок ранее',ids,index=max(0,len(ids)-2),format_func=opts.get,key='r31_custom_b')
  kwargs={'current':a,'base':b}
 context=M.choose(store,category,mode,**kwargs)
 receipt=M.dt.datetime.fromisoformat(context['a']['received_at'].replace('Z','+00:00')).astimezone(M.ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M МСК')
 st.caption(f"Рынок WB / {category}. Ранее: {context['b_start']} — {context['b_end']} → сейчас: {context['a']['start']} — {context['a']['end']}. Получен в каталог {receipt}; это не статистика одного дня.")
 for text in context['warnings']:st.warning(text)
 return context

def market_view(filters,cross=False):
 store=M.Store();context=market_context(store)
 if context is None:return
 index_seal=hashlib.sha256((store.root/'index.json').read_bytes()).hexdigest();start=time.perf_counter()
 with st.spinner('Рынок WB из подготовленного снимка…'):full=market_calc(index_seal,json.dumps(context,sort_keys=True))
 f=M.filter_rows(full,filters.get('phrase',''),filters.get('qclass',[]),unknown_kind=filters.get('unknown_kind','Все'))
 c1,c2=st.columns([3,2])
 with c1:signal=st.selectbox('Рыночная выборка',list(MARKET_KINDS),key='r31_market_kind')
 with c2:
  metric_choices={'Частотность WB':'frequency','Переходы в карточки WB':'clicks','Корзины WB':'carts','Заказали по отчёту WB':'orders'}
  metric='frequency' if cross else metric_choices[st.selectbox('Метрика рынка WB',list(metric_choices),key='r31_market_metric_box')]
 st.caption('WB целиком, выбранная категория. Товарные фильтры O’STIN не изменяют WB. Доли — в полной выгрузке категории, текст/класс не меняют знаменатель; категории не складываются.')
 if metric=='orders':st.caption('WB называет поле «Заказали товаров», но в описании указывает количество покупателей, купивших после поиска. Показатель сохраняется как метрика отчёта; это не подтверждённые штуки или финансовые продажи O’STIN.')
 with st.expander('Пороги значимости рыночного спроса',expanded=False):
  cols=st.columns(4)
  with cols[0]:mf=st.number_input('Минимальная частотность сейчас',min_value=0,value=1000,key='r31_mf')
  with cols[1]:mb=st.number_input('Минимальная база для относительного роста',min_value=0,value=100,key='r31_mb')
  with cols[2]:ma=st.number_input('Минимальный абсолютный прирост',min_value=0,value=100,key='r31_ma')
  with cols[3]:mp=st.number_input('Минимальный прирост, %',min_value=0.0,value=10.0,key='r31_mp')
  st.caption('Это регулируемые фильтры шума, не доказательство устойчивого тренда. По умолчанию первым идёт абсолютный прирост при существенном объёме.')
 kind=MARKET_KINDS[signal]
 if kind=='exited' and not context['full_structure']:st.info('Вышедшие фразы Н/Д: нужен второй полный снимок. Встроенные предыдущие значения его не заменяют.')
 selected=f if kind=='all' or context['single'] and kind!='exited' else M.rank(f,kind,mf,mb,ma,mp)
 relevance=V.assortment_context(selected,filters)
 if cross:
  st.markdown('**Рыночный спрос против присутствия O’STIN**')
  joined=V.cross_market(selected,context,filters)
  narrow=joined[['query_raw','frequency_B','frequency_A','frequency_delta','ostin_orders_B','ostin_orders_A','signal']].rename(columns={'query_raw':'Фраза','frequency_B':'Частотность WB ранее','frequency_A':'Частотность WB сейчас','frequency_delta':'Δ частотности WB','ostin_orders_B':'Заказы TOP-100 O’STIN ранее','ostin_orders_A':'Заказы TOP-100 O’STIN сейчас','signal':'Сигнал / ограничение'})
  table(narrow,'r31_cross',sort='Δ частотности WB')
  if len(joined):st.warning(joined.interpretation.iloc[0])
 else:
  st.markdown('**Один снимок — текущий факт, динамика Н/Д**' if context['single'] else '**Обрати внимание — приросло**' if kind=='absolute' else '**'+signal+'**')
  if len(relevance):
   v=relevance.iloc[0];count=v['Исторических моделей O’STIN по предмету'];count=None if pd.isna(count) else int(count)
   st.write(('**Текущий факт:** ' if context['single'] else '**Что изменилось:** ')+M.note(v,count))
  sort='Сейчас' if context['single'] or kind in ('stable','all','new') else 'Ранее' if kind=='exited' else 'Δ %' if kind=='relative' else 'Δ доли, п.п.' if kind=='share' else 'Δ ед.'
  table(V.market_compact(selected,metric),'r31_market_'+kind+'_'+metric,sort=sort)
  st.caption('Основной показатель поискового спроса — количество запросов. Переходы в карточки не равны числу запросов. Метрики «заказали» WB не являются заказами O’STIN.')
  with st.expander('Что проверить O’STIN — предмет, ассортимент, конверсии'):
   detail=relevance.copy()
   detail['Факт → значение → проверка']=np.select([detail.qclass.isin(['BRAND_PURE','BRAND_CATEGORY']),detail.qclass.eq('COMPETITOR'),detail.qclass.eq('UNKNOWN')],['Брендовая частотность WB не равна заказам O’STIN; проверить совместимые периоды и ассортимент.','Чужой бренд: проверить конкурентный спрос и релевантность предложения O’STIN, не подменять бренд.','Уточнить смысл и классификацию фразы; нужна ручная оценка.'],default='Проверить ассортимент, карточку, размеры, гендер, коллекцию и сезонность. Автоматическое увеличение бюджета не следует из частотности.')
   cols=['query_raw','top_subject','frequency_B','frequency_A','frequency_delta','Исторических моделей O’STIN по предмету','Примеры артикулов (история, не текущие остатки)','Дата ассортимента Search','Факт → значение → проверка']
   table(detail[cols],'r31_market_relevance',sort='frequency_delta')
 # Export full filtered rows and the exact visible compact selection. No fake B.
 joined=V.cross_market(selected,context,filters)
 meta=pd.DataFrame([{k:v for k,v in m.items() if k in ('id','report_type','category','received_at','start','end','previous_start','previous_end','original_file','sha256','rows','qa_status','schema_version','classifier_version','revision_of')} for m in store.snapshots(context['category'])]);meta['revision_of']=meta.revision_of.astype(str)
 meta['Создан на Google Drive (UTC)']=[m.get('source_provenance',{}).get('created_time','Н/Д — локальный импорт') for m in store.snapshots(context['category'])]
 meta['Активная классификация']=M.VERSION
 warnings=pd.DataFrame({'Предупреждение':context['warnings']+['Рынок WB не является заказами O’STIN. Доли рынка O’STIN не рассчитываются.','Нормализованные коллизии сохраняются отдельными исходными строками; частотность не склеивается.','Поле «Заказали товаров» WB описывает как число покупателей. Сохраняется исходная семантика/название, не переименовывается в финансовые продажи.']})
 export={'Растущий спрос WB':selected,'Экран спрос WB':V.market_compact(selected,metric),'WB и O’STIN':joined,'Ассортиментный контекст':relevance,'Снимки и периоды':meta,'Предупреждения рынка':warnings,'Аномалии рынка':f[f.conversion_anomaly_A|f.conversion_anomaly_B|f.normalization_ambiguous]}
 if not cross:export['Проверки по сигналам']=detail[cols]
 else:export['Экран WB и O’STIN']=narrow
 # Include O'STIN views as separate sheets, never a combined financial total.
 def package():
  a,b=A.weeks()[-1],A.weeks()[-2];ostin_s=calc(A.pointer()['revision'],a,b,json.dumps(filters,sort_keys=True),100,3);result=dict(export)
  result.update({k:v for k,v in ostin_s.items() if k in ('Точки входа','Брендовые запросы','Все запросы TOP100','Товары и возможности','Растущие запросы O’STIN','Диагностика R31')})
  result['Растущие запросы O’STIN']=V.ostin_signals(ostin_s)
  return result
 download(package,'MARKET_'+context['a']['sha256'][:8]+'_'+context['mode'])
 with st.expander('История снимков, категории, ограничения и исходные аномалии'):
  st.dataframe(meta,hide_index=True,width='stretch');st.dataframe(warnings,hide_index=True,width='stretch')
  table(export['Аномалии рынка'],'r31_market_anomalies',sort='frequency_A',height=300)
 st.caption(f"Снимок: {context['a']['id']} · строк {context['a']['rows']:,} · подготовленный слой/кеш {time.perf_counter()-start:.2f} с. Excel читается только при импорте.")

def render(context=None):
 st.header('Поиск и точки входа · R3.1')
 mode=st.segmented_control('Режим',MODES,default=MODES[0],key='r31_mode') or MODES[0]
 scope=st.radio('Область сигналов',['Запросы O’STIN','Спрос WB','WB / O’STIN'],horizontal=True,key='r31_scope') if mode=='Обрати внимание' else None
 filters=shared_filters()
 if mode=='Обрати внимание' and scope in ('Спрос WB','WB / O’STIN'):market_view(filters,scope=='WB / O’STIN')
 else:ostin(mode,filters)

calc.clear=lambda:None
market_calc.clear=lambda:None
