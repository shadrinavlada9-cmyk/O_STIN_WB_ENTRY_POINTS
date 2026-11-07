"""Per-table full XLSX via request-owned disk tickets, never browser-page slices."""
import streamlit as st
import pandas as pd,json
def table(frame,key,sort=None,height=430,default_ascending=False):
 from wb_search_r32.r32_tables import table as show
 from wb_search_r32.export_ticket import prepare
 show(frame,key,sort,height,default_ascending)
 chosen=st.session_state.get(key+'_sort');ascending=st.session_state.get(key+'_asc',default_ascending)
 ordered=frame.sort_values(chosen,ascending=ascending,na_position='last',kind='stable') if chosen in frame else frame
 context=st.session_state.get('public_export_context',{})
 metadata=pd.DataFrame([{'Параметр':k,'Значение':json.dumps(v,ensure_ascii=False,default=str) if isinstance(v,(dict,list,tuple)) else str(v)} for k,v in context.items()])
 ticket=prepare({'Таблица':ordered,'Периоды и фильтры':metadata})
 from .excel_controls import control
 control(ticket,'public_xlsx_'+key,'SEARCH_'+key+'.xlsx','все строки этой таблицы',context={'selection':context,'sort':chosen,'ascending':ascending})
