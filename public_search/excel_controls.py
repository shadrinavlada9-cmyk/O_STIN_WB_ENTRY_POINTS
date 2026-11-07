"""No idle polling. Pending status and ready-file delivery are separate actions."""
import streamlit as st
from .excel_jobs import manager,identity

def panel(job,key,filename,scope,request=None):
 jobs=manager();initial=jobs.status(job,verify=True);pending=initial['state'] in ('QUEUED','RUNNING','BUSY')
 if pending:jobs.arm(job)
 @st.fragment(run_every=3 if pending else None)
 def show():
  state=jobs.status(job,verify=True)
  if pending and state['state'] not in ('QUEUED','RUNNING','BUSY'):st.rerun(scope='app')
  with st.container(key='excel_job_'+key):
   if state['state']=='READY':
    try:data=jobs.ready_bytes(job)
    except (RuntimeError,TimeoutError):state={'state':'INVALID'}
    else:
     st.caption('Excel готов · '+str(len(state['sheets']))+' листов · '+f"{state['bytes']/1024**2:.2f}".replace('.',',')+' МиБ. Повторное скачивание не запускает расчёт.')
     st.download_button('Скачать Excel — '+scope,data=data,file_name=state.get('name',filename),mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',key=key+'_ready',on_click='ignore');return
   if state['state'] in ('QUEUED','RUNNING','BUSY'):
    text='В очереди подготовки Excel…' if state['state']=='QUEUED' else 'Проверка целостности полного Excel…' if state.get('phase')=='VERIFY' else 'Подготовка полного Excel… Можно обновить страницу: задание продолжится в этом контейнере.'
    st.caption(text)
   else:
    if state['state'] in ('ERROR','INTERRUPTED','INVALID'):st.warning('Подготовка Excel не завершена или кеш повреждён. Данные не изменены.')
    if request is None:
     st.caption('Кеш контейнера мог быть очищен. Выберите нужные фильтры и подготовьте Excel в разделе заново.');return
    if st.button('Подготовить Excel — '+scope,key=key+'_prepare'):
     try:jobs.submit(job,request.build if hasattr(request,'build') else request.to_excel,defer_start=True,filename=filename)
     except RuntimeError as error:st.warning(str(error));return
     seen=list(st.session_state.get('_xlsx_seen',[]));seen.append(job);st.session_state['_xlsx_seen']=seen[-8:]
     st.query_params['excel_job']=job
     mode=st.session_state.get('public_mode')
     if mode:st.query_params['excel_view']=mode
     st.caption('Задание Excel принято. Обновляется состояние подготовки…');st.rerun(scope='app')
 show()

def control(request,key,filename,scope,context=None):
 context={'selection':context if context is not None else st.session_state.get('public_export_context',{}),'data_version':st.session_state.get('public_data_version'),'mode':st.session_state.get('public_mode'),'scope':scope,'name':filename}
 panel(identity(request,context),key,filename,scope,request)

def resume():
 job=st.query_params.get('excel_job','')
 if len(job)!=64 or any(c not in '0123456789abcdef' for c in job) or job in st.session_state.get('_xlsx_seen',[]):return
 st.caption('Сохранённое задание Excel предыдущего среза. Текущие фильтры не меняют этот файл.')
 panel(job,'restored_job','Search.xlsx','сохранённое задание')
 if st.button('Скрыть сохранённое задание',key='hide_restored_job'):
  del st.query_params['excel_job'];st.rerun()
