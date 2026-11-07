"""The accepted Entry Points tables, without other Search/finance routes."""
from dataclasses import dataclass
from pathlib import Path
import json,os,logging
import pandas as pd
import streamlit as st
from . import accepted as A
from .analysis import Session
from .dataset import Feed
from .classifier import LABELS
from .tables import number,excel
from public_search.downloads import table
LOG=logging.getLogger('entry_points')
def compact(f,metric='orders',ids=None):
 out=A.compact(f,metric,ids)
 if 'Флаги A / B' in out:out['Аномалии сейчас / ранее']=out.pop('Флаги A / B').map({'A+B':'Обе недели','A':'Сейчас','B':'Ранее','':'Нет'})
 return out
def conversions(f,ids):
 out=compact(f,ids=ids);cols=[c for c in f if any(x in c for x in (' / ','CTR '))]
 return pd.concat([out[['Фраза / источник']],f[cols]],axis=1)
def views(frame,prefix,ids):
 out={prefix+' полные поля':frame}
 for metric,name in [('orders','Заказы'),('clicks','Переходы'),('carts','Корзины')]:out[prefix+' — '+name]=compact(frame,metric,ids)
 out[prefix+' — Конверсии']=conversions(frame,ids)
 if 'Показы A' in frame:
  f=compact(frame,ids=ids)[['Фраза / источник']]
  for c in ['Показы B','Показы A','Δ показы','Δ показы, %']:f[c]=frame[c]
  out[prefix+' — Показы']=f
 return out
@dataclass(frozen=True)
class Export:
 root:str|None
 url:str|None
 cache:str
 index_json:str
 a:str
 b:str
 filters_json:str
 selected:tuple|None
 def build(self):
  try:return self._build()
  except Exception as error:
   from . import diagnostics
   diagnostics.error(error,operation='entry_points.excel')
   raise RuntimeError('Не удалось сформировать Excel. Повторите после обновления страницы; данные не изменены.') from None
 def _build(self):
  feed=Feed(self.root,self.url,self.cache);index=json.loads(self.index_json)
  with Session(feed,index,self.a,self.b,json.loads(self.filters_json)) as session:
   f=session.summary();s=views(f,'Общая динамика точек входа',['Раздел','Точка входа'])
   for positive,name in [(True,'Какие точки входа дали рост заказов'),(False,'Какие точки входа дали снижение заказов')]:
    subset=f[f['Δ заказы'].gt(0) if positive else f['Δ заказы'].lt(0)].sort_values('Δ заказы',ascending=not positive)
    s[name]=compact(subset,ids=['Раздел','Точка входа'])
   if self.selected:s.update(views(session.detail(*self.selected),'Товары выбранной точки входа',['seller_article','nm_id']))
   s['Диагностика Портрета']=session.diagnostics();s['Флаги показов']=session.flags()
   s['Влияние аномалий Портрета']=session.anomaly_impact();s['Аномальные строки Портрета']=session.anomaly_rows()
   s['Источники и периоды']=pd.DataFrame([{k:r[k] for k in ['week','start','end','source_status']} for r in index['weeks'] if r['week'] in [self.a,self.b]])
   s['Фильтры и ограничения']=pd.DataFrame({'Параметр':['Неделя сейчас','Неделя ранее','Фильтры','Знаменатель','Ограничения'], 'Значение':[self.a,self.b,self.filters_json,'Полный Портрет выбранных товаров; класс/фраза/точка входа не меняют знаменатель.','Нет строки — Н/Д, а не ноль. Аномалии сохранены, не доказанная причина. Платная/органическая атрибуция не добавлялась.']})
   return excel(s)
def render():
 st.set_page_config(page_title='Точки входа O’STIN × WB',layout='wide')
 st.title('Точки входа · O’STIN × Wildberries')
 root=os.environ.get('ENTRY_DATA_ROOT');url=os.environ.get('ENTRY_INDEX_URL')
 if not root and not url:
  try:url=st.secrets.get('entry_index_url')
  except st.errors.StreamlitSecretNotFoundError:pass
 preview=os.environ.get('ENTRY_PREVIEW','1' if root else '0')=='1'
 if preview:st.warning('WORK PREVIEW R2. Расширение поисковой аналитики разрабатывается изолированно; работающий публичный feed не изменён. Ожидается независимый аудит кандидата.')
 st.caption('Отчёт «Портрет покупателя», только товары O’STIN. Заказы — штуки по методике отчёта, не финансовые заказы WB в рублях.')
 feed=Feed(root,url,os.environ.get('ENTRY_CACHE_ROOT'))
 index=feed.index();records={r['week']:r for r in index['weeks']};weeks=sorted(records)
 if not preview and index.get('status')!='PUBLIC_SCOPE_APPROVED':raise ValueError('Публичный состав ещё не согласован')
 if index.get('runtime_warning'):st.warning(index['runtime_warning'])
 c1,c2=st.columns(2)
 def week_label(w):return w+' · '+records[w]['start']+' — '+records[w]['end']
 with c1:a=st.selectbox('Неделя сейчас',weeks,index=len(weeks)-1,format_func=week_label,key='entry_a')
 with c2:b=st.selectbox('Неделя сравнения',weeks,index=max(0,len(weeks)-2),format_func=week_label,key='entry_b')
 st.caption('Фактический охват: '+records[weeks[0]]['start']+' — '+records[weeks[-1]]['end']+'. '+index['missing_next'])
 import hashlib
 st.session_state['public_data_version']=hashlib.sha256(json.dumps(index,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
 filters={}
 with st.expander('Фильтры O’STIN — фраза, класс и товары'):
  columns=st.columns(3)
  for i,(key,title) in enumerate([('gender','Гендер'),('collection','Коллекция'),('product_type','Тип товара')]):
   options=sorted({v for r in records.values() for v in r['facets'][key]})
   with columns[i]:filters[key]=st.multiselect(title,options,key='filter_'+key)
  for i,(key,title) in enumerate([('seller_article','Артикул продавца (точно)'),('nm_id','nmID WB (точно)'),('cm','ЦМ (подтверждённая связь)')]):
   with columns[i]:filters[key]=[v.strip() for v in st.text_input(title,key='filter_'+key).split(',') if v.strip()]
  with columns[0]:filters['qclass']=st.multiselect('Класс запроса',list(LABELS),format_func=LABELS.get,key='filter_class')
  with columns[1]:filters['phrase']=st.text_input('Поиск по фразе',key='filter_phrase')
  with columns[2]:filters['unknown_kind']=st.selectbox('Подтип неоднозначных запросов',['Все','Смешанные / конфликтные','Прочие неопределённые'])
  filters['entry']=st.multiselect('Точка входа O’STIN',sorted({v for r in records.values() for v in r['entry_points']}),key='filter_entry')
  st.caption('Класс и фраза отбирают связанные товары; показатели остаются из Портрета. Метрики TOP-100 не переносятся и не прибавляются.')
 st.session_state['public_export_context']={'Источник':'Портрет O’STIN','Неделя сейчас':a,'Неделя ранее':b,'Период сейчас':[records[a]['start'],records[a]['end']],'Период ранее':[records[b]['start'],records[b]['end']],'Фильтры':filters}
 with Session(feed,index,a,b,filters) as session:
  f=session.summary();search=f[f['Точка входа'].eq('Поисковая выдача')];recommend=f[f['Раздел'].eq('Карточка товара')&f['Точка входа'].eq('Подборки и рекомендации')]
  if len(search) and len(recommend):
   x,y=search.iloc[0],recommend.iloc[0]
   st.write('**Что изменилось:** поисковая выдача: '+number(x['Δ заказы'],'Δ')+' заказов, '+number(x['Δ доли, п.п.'],'Δ п.п.')+' доли; рекомендации в карточке: '+number(y['Δ заказы'],'Δ')+' заказов, '+number(y['Δ доли, п.п.'],'Δ п.п.')+' доли. Изменение объёма и вклада — разные сигналы; это не доказанная причина.')
  for name,frame in views(f,'Общая динамика точек входа',['Раздел','Точка входа']).items():
   if name.endswith('полные поля'):continue
   st.subheader(name);table(frame,'entry_'+name,'Δ ед.')
  st.caption('Конверсии пересчитаны по суммам. Исходные аномалии сохранены. Родительские и дочерние уровни не складываются повторно.')
  for positive,name in [(True,'Какие точки входа дали рост заказов'),(False,'Какие точки входа дали снижение заказов')]:
   subset=f[f['Δ заказы'].gt(0) if positive else f['Δ заказы'].lt(0)].sort_values('Δ заказы',ascending=not positive)
   st.subheader(name);table(compact(subset,ids=['Раздел','Точка входа']),'direction_'+str(positive),'Δ ед.',default_ascending=not positive)
  selected=None;st.subheader('Точка входа → товары')
  if len(f):
   pairs=list(zip(f['Раздел'],f['Точка входа']));selected=st.selectbox('Выберите точку входа',pairs,format_func=lambda p:' · '.join(p),key='selected_entry')
   detail=session.detail(*selected)
   for name,frame in views(detail,'Товары выбранной точки входа',['seller_article','nm_id']).items():
    if name.endswith('полные поля'):continue
    st.subheader(name);table(frame,'detail_'+name,'Δ ед.')
  with st.expander('Аномалии и ограничения — исходные значения не исправлялись'):
   st.warning('Аномалии текущей и предыдущей недель включены в факты. Значения выше 100% не обрезаются; они не являются основанием для автоматических рекомендаций.')
   table(session.flags(),'flags');table(session.diagnostics(),'diagnostics')
   st.caption('Диагностика полного Портрета выбранных товаров; класс/фраза не меняют эту базу. В Excel — исходные аномальные строки только Портрета, без других режимов Search.')
   table(session.anomaly_impact(),'anomaly_impact')
  request=Export(str(feed.root) if feed.root else None,feed.url,str(feed.cache),json.dumps(index,ensure_ascii=False,sort_keys=True),a,b,json.dumps(filters,ensure_ascii=False,sort_keys=True),selected)
  feed.remember(index)
 from public_search.excel_controls import control
 control(request,'entry_excel','OSTIN_ENTRY_POINTS_'+a+'_'+b+'.xlsx','все таблицы выбранного среза',context={'kind':'Portrait','a':a,'b':b,'filters':filters,'selected':selected})
 from public_search.raw_export import EntryRaw
 raw=EntryRaw(request.root,request.url,request.cache,request.index_json,request.a,request.b,request.filters_json)
 st.download_button('⬇ CSV/ZIP — все исходные строки Портрета по выбранным фильтрам',data=raw.build,file_name='PORTRAIT_FULL_FILTERED.zip',key='entry_raw_full',on_click='ignore')
