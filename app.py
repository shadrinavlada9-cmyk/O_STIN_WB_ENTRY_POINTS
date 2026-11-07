"""Isolated expansion of the existing public app. No private WB bootstrap."""
from pathlib import Path
import os,json,tempfile
import streamlit as st
st.set_page_config(page_title='Поисковая аналитика O’STIN × WB',layout='wide')
MODES=['Точки входа','Поисковые запросы O’STIN','Поисковые запросы — итого WB','Какие товары требуют внимания','Обрати внимание']
restored_mode=st.query_params.get('excel_view','')
mode=st.selectbox('Раздел поисковой аналитики',MODES,index=MODES.index(restored_mode) if restored_mode in MODES else 0,key='public_mode')
placeholder=Path(os.environ.get('PUBLIC_SEARCH_CACHE',str(Path(tempfile.gettempdir())/'public_search_r2_runtime')))/'empty'
placeholder.mkdir(parents=True,exist_ok=True);os.environ.setdefault('WB_SEARCH_DATA_ROOT',str(placeholder))
os.environ.setdefault('WB_ANALIT_HOME',str(placeholder))
from public_search.excel_controls import resume
resume()
def config(name):
 try:return st.secrets.get(name)
 except st.errors.StreamlitSecretNotFoundError:return None
try:
 if mode==MODES[0]:
  from entry_points.view import render
  render()
 else:
  from public_search.feed import SearchFeed
  feed=SearchFeed(os.environ.get('PUBLIC_SEARCH_ROOT'),config('search_index_url'),os.environ.get('PUBLIC_SEARCH_CACHE'))
  st.session_state['public_data_version']=[(r['kind'],r['sha256']) for r in feed.index['components']]
  from wb_search_r32 import analytics as A,market as M,page_r32 as P
  with A.SD.GATE:
   if mode in (MODES[2],MODES[4]):
    market_root=feed.ensure('MARKET');M.ROOT=market_root
    st.header(mode);P.market_view(all_rows=mode==MODES[2])
   else:
    ostin_root=feed.ensure('OSTIN');A.ROOT=ostin_root
    st.header(mode)
    if mode==MODES[1]:
     selection=st.radio('Аналитика запросов O’STIN',['Все классы запросов','Брендовый разбор'],horizontal=True,key='ostin_submode')
     own=P.MODES[2] if selection=='Все классы запросов' else P.MODES[1]
    else:own=P.MODES[3]
    filters=P.filters();P.ostin(own,filters)
    from public_search.raw_export import OstinRaw
    raw=OstinRaw(str(ostin_root),st.session_state['r31_a'],st.session_state['r31_b'],json.dumps(filters,ensure_ascii=False),('sq','ep') if own==P.MODES[3] else ('sq',),own==P.MODES[3])
    st.download_button('⬇ CSV/ZIP — все исходные строки выбранного среза, без ограничения страницы',data=raw.build,file_name='OSTIN_SEARCH_FULL_FILTERED.zip',key='ostin_raw_full',on_click='ignore')
   A.SD.release()
except Exception as error:
 from entry_points import diagnostics
 diagnostics.error(error,operation='public_search.render')
 st.error('Не удалось открыть проверенные поисковые данные. Рабочие данные не изменены. Подробности сохранены в журнале.')
