"""Presentation only: Russian numbers, native scrolling and numeric Excel cells."""
import html,io,math,re
import numpy as np
import pandas as pd
import streamlit as st
from . import accepted as A

NAMES={'query_raw':'Поисковый запрос','query_norm':'Нормализованная фраза','qclass':'Класс запроса','qrule':'Правило классификации','category':'Категория WB','top_subject':'Основной предмет','frequency':'Частотность','clicks':'Переходы','carts':'Корзины','orders':'Заказали по отчёту WB','daily':'Запросов в день','products':'Количество товаров','subjects':'Предметов с заказами','conversion_anomaly':'Аномалия конверсии','normalization_ambiguous':'Неоднозначная нормализация','query_key':'Ключ исходной фразы','eligible_demand':'Допустимо сравнение спроса','share_basis':'Основание сравнения','classifier_version':'Версия классификации','active_classifier_version':'Активная версия классификации','cart_cr_source':'Конверсия в корзину WB, %','order_cr_source':'Конверсия в заказ WB, %','cart_cr':'Корзины / переходы, %','order_cr':'Заказы / корзины, %','end_to_end_cr':'Заказы / переходы, %','status':'Статус сравнения','id':'ID снимка','received_at':'Дата получения снимка','start':'Начало периода','end':'Конец периода','previous_start':'Начало предыдущего периода','previous_end':'Конец предыдущего периода','original_file':'Исходный файл','sha256':'SHA256 источника','rows':'Строк источника','schema_version':'Версия схемы','qclass_snapshot':'Класс в исходном снимке','qrule_snapshot':'Правило в исходном снимке','source_cart_cr':'Исходная конверсия в корзину, %','source_order_cr':'Исходная конверсия в заказ, %','source_ctr':'Исходный CTR, %','cm_method':'Подтверждение связи ЦМ'}
def label(c):
 if c=='anomaly':return 'Флаг аномалии показов'
 if c=='phrase_key':return 'Ключ сопоставления фразы'
 c=NAMES.get(c,A.DISPLAY_NAMES.get(c,c))
 for suffix,ru in (('_A',' сейчас'),('_B',' ранее'),('_delta',' — изменение'),('_pct',' — изменение, %')):
  if c.endswith(suffix):
   stem=c[:-len(suffix)];c=NAMES.get(stem,stem)+ru;break
 c=c.replace(' A,',' сейчас,').replace(' B,',' ранее,')
 if c.endswith(' A'):c=c[:-2]+' сейчас'
 if c.endswith(' B'):c=c[:-2]+' ранее'
 return c
def kind(c):
 c=str(c);change=c.startswith(('Δ','Изменение','Вклад в Δ')) or c.endswith(('_delta','_pct'))
 pp='п.п.' in c or '_share_pp' in c
 percent=('%' in c or c.endswith('_pct') or '_cr_' in c) and not pp
 return change,pp,percent
def number(v,c=''):
 if v is None or v is pd.NA or (isinstance(v,(float,np.floating)) and not math.isfinite(v)):return 'Н/Д'
 if isinstance(v,(bool,np.bool_)):return 'Да' if v else 'Нет'
 if not isinstance(v,(int,float,np.number)):return str(v)
 change,pp,percent=kind(c);dec=2 if pp or percent else 0
 sign='+' if change and v>0 else '−' if v<0 else ''
 return sign+f'{abs(v):,.{dec}f}'.replace(',',' ').replace('.',',')+(' п.п.' if pp else '%' if percent else '')
def table_html(frame,key,height=430):
 heads=''.join('<th>'+html.escape(label(c))+'</th>' for c in frame)
 body=[]
 for row in frame.itertuples(index=False,name=None):
  cells=[]
  for c,v in zip(frame,row):
   numeric=isinstance(v,(int,float,np.number)) and not isinstance(v,(bool,np.bool_));change,_,_=kind(c)
   color=' up' if numeric and change and pd.notna(v) and v>0 else ' down' if numeric and change and pd.notna(v) and v<0 else ''
   cells.append(f'<td class="{"num" if numeric or pd.isna(v) else "text"}{color}">'+html.escape(number(v,c))+'</td>')
  body.append('<tr>'+''.join(cells)+'</tr>')
 return f'''<style>
.r32-scroll{{overflow:auto!important;width:100%;border:1px solid #d8dce4;border-radius:6px;scrollbar-gutter:stable;scrollbar-width:auto;}}
.r32-scroll::-webkit-scrollbar{{width:14px;height:14px}}.r32-scroll::-webkit-scrollbar-thumb{{background:#8190a5;border:3px solid #edf0f4;border-radius:8px}}.r32-scroll::-webkit-scrollbar-track{{background:#edf0f4}}
.r32-table{{border-collapse:separate;border-spacing:0;min-width:100%;font-size:13px;font-variant-numeric:tabular-nums;font-family:Arial,sans-serif}}
.r32-table td,.r32-table th{{padding:8px 10px;border-bottom:1px solid #e5e7eb;border-right:1px solid #eef0f3;white-space:nowrap;text-align:right;}}
.r32-table th{{position:sticky;top:0;background:#f1f4f8;color:#334155;z-index:2;font-weight:600}}
.r32-table td:first-child,.r32-table th:first-child{{position:sticky;left:0;text-align:left;background:#fff;z-index:1;min-width:235px;max-width:370px;white-space:normal;overflow-wrap:anywhere}}
.r32-table th:first-child{{z-index:3;background:#f1f4f8}}
.r32-table .text{{text-align:left;max-width:400px;white-space:normal;min-width:125px}}
.r32-table .num{{text-align:right;min-width:82px}}.r32-table .up{{color:#24784b}}.r32-table .down{{color:#a84949}}
</style><div class="r32-scroll" style="max-height:{height}px" data-r32="{html.escape(key)}" role="region" aria-label="Таблица с горизонтальной прокруткой" tabindex="0"><table class="r32-table"><thead><tr>{heads}</tr></thead><tbody>{''.join(body)}</tbody></table></div>'''
def table(frame,key,sort=None,height=430,default_ascending=False):
 if frame.empty:st.info('Нет строк по выбранным условиям. Отсутствие в выборке не означает нулевой спрос.');return
 nums=[c for c in frame if pd.api.types.is_numeric_dtype(frame[c]) and not pd.api.types.is_bool_dtype(frame[c])]
 c1,c2,c3=st.columns([3,1,1])
 with c1:chosen=st.selectbox('Сортировка',nums,format_func=label,index=nums.index(sort) if sort in nums else 0,key=key+'_sort') if nums else None
 with c2:ascending=st.checkbox('По возрастанию',value=default_ascending,key=key+'_asc')
 maximum=max(1,(len(frame)+99)//100)
 if st.session_state.get(key+'_page',1)>maximum:st.session_state[key+'_page']=1
 with c3:page=st.number_input('Страница',1,maximum,1,key=key+'_page')
 # Sort only the key/row positions. Gather at most one visible page, never
 # an entire wide frame. Stable ties and NaN-last semantics are unchanged.
 order=frame[chosen].reset_index(drop=True).sort_values(ascending=ascending,na_position='last',kind='stable').index.to_numpy() if chosen else None
 start=(page-1)*100;stop=page*100
 rows=frame.iloc[order[start:stop]] if order is not None else frame.iloc[start:stop]
 st.caption(number(len(frame))+' строк · до 100 на странице · Excel содержит все строки среза. Для широкой таблицы: полоса внизу или Shift + колесо; первая колонка закреплена.')
 st.html(table_html(rows,key,height))
def excel(sheets):
 import xlsxwriter
 out=io.BytesIO()
 with xlsxwriter.Workbook(out,{'constant_memory':True,'strings_to_formulas':False,'strings_to_urls':False}) as book:
  head=book.add_format({'bold':True,'bg_color':'#eef1f6','text_wrap':True,'valign':'top'});formats={}
  used=set()
  for name,frame in sheets.items():
   if callable(frame):frame=frame()
   safe=re.sub(r'[\[\]:*?/\\]',' ',name)[:31]
   if safe.casefold() in used:safe=safe[:26]+'_'+str(len(used))
   used.add(safe.casefold())
   ws=book.add_worksheet(safe);ws.write_row(0,0,[label(c) for c in frame],head);ws.set_row(0,36)
   for i,row in enumerate(frame.itertuples(index=False,name=None),1):
    for j,(c,v) in enumerate(zip(frame,row)):
     if v is None or v is pd.NA or isinstance(v,(float,np.floating)) and not math.isfinite(v):continue
     if isinstance(v,(bool,np.bool_)):ws.write_string(i,j,'Да' if v else 'Нет');continue
     if isinstance(v,np.generic):v=v.item()
     if isinstance(v,(int,float)):
      change,pp,pct=kind(c);groups=max(1,(len(str(int(abs(v))))-1)//3)
      base=('# '+('### '*(groups-1))+'##0') if abs(v)>=1000 else '0'
      if pp or pct:base+='.00'
      unit='" п.п."' if pp else '"%"' if pct else ''
      fmt=('"+"' if change else '')+base+unit+';"−"'+base+unit+';'+base+unit
      color='#24784b' if change and v>0 else '#a84949' if change and v<0 else '#202632'
      key=(fmt,color)
      if key not in formats:formats[key]=book.add_format({'num_format':fmt,'font_color':color,'align':'right','font_name':'Consolas'})
      ws.write_number(i,j,v,formats[key])
     else:ws.write_string(i,j,str(v))
   ws.freeze_panes(1,1);ws.autofilter(0,0,len(frame),max(0,len(frame.columns)-1));ws.set_column(0,0,43);ws.set_column(1,max(1,len(frame.columns)-1),21)
 return out.getvalue()
