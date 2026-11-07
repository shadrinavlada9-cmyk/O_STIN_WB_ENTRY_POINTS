"""Append-only WB market snapshots and comparisons; separate from O'STIN facts."""
from pathlib import Path
from functools import lru_cache
import datetime as dt,hashlib,io,json,os,re,shutil,time,uuid,zipfile
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np
from .classifier import classify,normalize,VERSION,apply_frame
from .import_weekly import Workbook,number,atomic
ROOT=Path(os.environ['WB_SEARCH_DATA_ROOT']).resolve()
SCHEMA='WB_MARKET_CATEGORY_V1'
TYPE='WB_MARKET_SEARCH_CATEGORY'
FIELDS={'frequency':'Количество запросов','daily':'Запросов в среднем за день','clicks':'Перешли в карточку товара','carts':'Добавили в корзину','orders':'Заказали товаров','cart_cr_source':'Конверсия в корзину','order_cr_source':'Конверсия в заказ','subjects':'Предметов с заказами по запросу','products':'Количество товаров'}
LABELS={'frequency':'Частотность WB','clicks':'Переходы в карточки WB','carts':'Корзины WB','orders':'Заказали по отчёту WB'}
sha=lambda b:hashlib.sha256(b).hexdigest()
def received_month(meta):return dt.datetime.fromisoformat(meta['received_at'].replace('Z','+00:00')).astimezone(ZoneInfo('Europe/Moscow')).strftime('%Y-%m')
def dates(text):
 found=re.findall(r'\d{2}-\d{2}-\d{4}|\d{4}-\d{2}-\d{2}',str(text))
 if len(found)!=2:raise ValueError('Нет двух дат внутри рыночного отчёта')
 return [dt.datetime.strptime(s,'%Y-%m-%d' if len(s.split('-')[0])==4 else '%d-%m-%Y').date().isoformat() for s in found]
def parse(blob):
 start=time.perf_counter()
 with zipfile.ZipFile(io.BytesIO(blob)) as z:
  if z.testzip():raise ValueError('Повреждён ZIP рынка')
  members=[n for n in z.namelist() if n.lower().endswith('.xlsx')]
  if len(members)!=1:raise ValueError('Рынок: нужен один однозначный XLSX')
  with Workbook(z.read(members[0])) as book:
   required={'Общая информация','Метрики','Детальная информация'}
   if not required.issubset(book.sheet_names):raise ValueError('Это не отчёт «Поисковые запросы по площадке»')
   info={str(r[0]):str(r[1]) for _,r in book.rows('Общая информация') if len(r)>1 and r[1] is not None}
   a,b=dates(info.get('Выбранный период'));pa,pb=dates(info.get('Предыдущий период'))
   category=info.get('Категория','').strip()
   if not category or dt.date.fromisoformat(b)<dt.date.fromisoformat(a) or dt.date.fromisoformat(pb)<dt.date.fromisoformat(pa):raise ValueError('Не подтверждены категория / периоды рынка')
   rows=iter(book.rows('Детальная информация'));header=None
   for _,row in rows:
    if 'Поисковый запрос' in row and 'Количество запросов' in row:header=row;break
   if header is None or len(set(header))!=len(header):raise ValueError('Нет однозначного заголовка рынка')
   f=pd.DataFrame([r+[None]*(len(header)-len(r)) for _,r in rows if any(x is not None for x in r)],columns=header)
   metrics=[r for _,r in book.rows('Метрики')]
   sheets=book.sheet_names
 for name in FIELDS.values():
  if name not in f or name+' (предыдущий период)' not in f:raise ValueError('Рынок: отсутствует поле '+name)
 if f['Поисковый запрос'].isna().any():
  # Empty text remains UNKNOWN, never brand; different empty rows are ambiguous.
  f['Поисковый запрос']=f['Поисковый запрос'].fillna('')
 raw=f.copy();f['query_raw']=f['Поисковый запрос'].astype(str)
 if f.query_raw.duplicated().any():raise ValueError('Дубли физической фразы рынка — импорт остановлен')
 classified=pd.DataFrame([classify(t) for t in f.query_raw],columns=['query_norm','qclass','qrule'],index=f.index)
 f=pd.concat([f,classified],axis=1)
 for key,name in FIELDS.items():
  for suffix,label in (('A',name),('embedded_B',name+' (предыдущий период)')):
   f[key+'_'+suffix]=number(f[label])
   invalid=f[label].notna()&f[label].astype(str).str.strip().ne('')&f[key+'_'+suffix].isna()
   if invalid.any():raise ValueError('Рынок: нечисловое значение в '+label)
   if f[key+'_'+suffix].isna().any():raise ValueError('Рынок: неполное обязательное поле '+label)
 if (f[['frequency_A','frequency_embedded_B']]<0).any().any():raise ValueError('Отрицательная частотность')
 f['top_subject']=f['Больше всего заказов в предмете'].fillna('Не определено')
 f['normalization_ambiguous']=f.query_norm.duplicated(keep=False)
 f['key']=f.query_norm.where(~f.normalization_ambiguous,f.query_norm+' ␟ '+f.query_raw)
 if f.key.duplicated().any():raise ValueError('Неоднозначный ключ рынка')
 for s in ('A','embedded_B'):
  f['conversion_anomaly_'+s]=(f['cart_cr_source_'+s]>100)|(f['order_cr_source_'+s]>100)|(f['carts_'+s]>f['clicks_'+s])|(f['orders_'+s]>f['carts_'+s])
 # frequency and card transitions have different semantics: clicks>frequency
 # is explicitly NOT an anomaly and is never used as a search conversion.
 meta={'report_type':TYPE,'category':category,'start':a,'end':b,'previous_start':pa,'previous_end':pb,'rows':len(f),'schema_version':SCHEMA,'classifier_version':VERSION,'qa_status':'PASS','source_status':'COMPLETE_FILE','sheets':sheets,'columns':header,'metric_definitions':metrics,'normalization_ambiguous_rows':int(f.normalization_ambiguous.sum()),'totals_current':{k:float(f[k+'_A'].sum()) for k in LABELS},'embedded_previous_scope':'ONLY_CURRENT_REPORT_PHRASES_NOT_FULL_HISTORY','import_parse_seconds':time.perf_counter()-start}
 return f,meta

class Store:
 def __init__(self,path=None):
  self.root=Path(path or ROOT/'market_data')
  if not self.root.is_dir():self.root.mkdir(parents=True,exist_ok=True)
 def index(self):
  p=self.root/'index.json'
  return json.loads(p.read_text(encoding='utf8')) if p.exists() else {'schema':SCHEMA,'snapshots':[],'monthly_baselines':{}}
 def metadata(self,snapshot_id):
  record=next((r for r in self.index()['snapshots'] if r['id']==snapshot_id),None)
  if record is None:raise ValueError('Снимок не принят в каталоге')
  path=self.root/'snapshots'/record['sha256']/'meta.json'
  if sha(path.read_bytes())!=record['metadata_sha256']:raise ValueError('Изменена неизменяемая метаинформация снимка')
  return json.loads(path.read_text(encoding='utf8'))
 def snapshots(self,category=None):
  rows=[self.metadata(r['id']) for r in self.index()['snapshots']]
  return sorted([r for r in rows if category is None or r['category']==category],key=lambda r:(r['received_at'],r['sequence']))
 def accept(self,path,*,received_at=None,provenance=None,crash_at=None):
  path=Path(path);blob=path.read_bytes();digest=sha(blob);index=self.index()
  old=next((r for r in index['snapshots'] if r['sha256']==digest),None)
  if old:
   meta=self.metadata(old['id']);oldfolder=self.root/'snapshots'/digest
   if sha((oldfolder/'original.zip').read_bytes())!=digest or sha((oldfolder/'rows.parquet').read_bytes())!=meta['rows_sha256']:raise ValueError('Повреждён уже принятый рыночный снимок')
   return {'status':'NO_CHANGES','snapshot':meta}
  folder=self.root/'snapshots'/digest;folder.parent.mkdir(exist_ok=True)
  if folder.exists():
   meta=json.loads((folder/'meta.json').read_text(encoding='utf8'))
   if meta['sha256']!=digest or sha((folder/'rows.parquet').read_bytes())!=meta['rows_sha256']:raise ValueError('Незавершённый/повреждённый снимок')
  else:
   rows,meta=parse(blob)
   if sha(path.read_bytes())!=digest:raise ValueError('Рыночный источник изменился при обработке')
   prior=[self.metadata(r['id']) for r in index['snapshots']]
   corrections=[r['id'] for r in prior if (r['category'],r['start'],r['end'])==(meta['category'],meta['start'],meta['end'])]
   meta.update(id='WBMARKET_'+digest,sha256=digest,original_file=path.name,received_at=received_at or dt.datetime.now(dt.timezone.utc).isoformat(),sequence=len(index['snapshots'])+1,source_provenance=provenance or {'local_path':str(path),'source_mtime':dt.datetime.fromtimestamp(path.stat().st_mtime,dt.timezone.utc).isoformat()},revision_of=corrections)
   temp=self.root/('.stage_'+uuid.uuid4().hex);temp.mkdir()
   rows.to_parquet(temp/'rows.parquet',index=False)
   (temp/'original.zip').write_bytes(blob)
   meta['rows_sha256']=sha((temp/'rows.parquet').read_bytes())
   atomic(temp/'meta.json',meta)
   if crash_at=='before_snapshot_commit':raise RuntimeError('TEST_INTERRUPTION_BEFORE_SNAPSHOT_COMMIT')
   temp.rename(folder)
  if sha(path.read_bytes())!=digest:raise ValueError('Рыночный источник изменился до принятия')
  if crash_at=='before_index_commit':raise RuntimeError('TEST_INTERRUPTION_BEFORE_INDEX_COMMIT')
  # Append-only snapshot; correction never replaces the previous folder or base.
  index['snapshots'].append({'id':meta['id'],'sha256':digest,'metadata_sha256':sha((folder/'meta.json').read_bytes())})
  month=received_month(meta);key=meta['category']+'|'+month
  index['monthly_baselines'].setdefault(key,meta['id'])
  if (self.root/'index.json').exists():
   previous=(self.root/'index.json').read_bytes();history=self.root/'index_history';history.mkdir(exist_ok=True)
   target=history/(sha(previous)+'.json')
   if not target.exists():target.write_bytes(previous)
  atomic(self.root/'index.json',index)
  return {'status':'ACCEPTED','snapshot':meta}
 def rows(self,snapshot_id):
  meta=self.metadata(snapshot_id);path=self.root/'snapshots'/meta['sha256']/'rows.parquet'
  return _rows(str(path),meta['rows_sha256'],VERSION)

def _rows(path,digest,version):
 p=Path(path)
 if sha(p.read_bytes())!=digest:raise ValueError('Изменены строки принятого рыночного снимка')
 from .selected_data import arrow_type
 import pyarrow.parquet as pq
 # Comparison needs prepared metrics, not a second set of the same metrics
 # under original XLSX labels. Public data files still retain every field.
 columns=['query_norm','query_raw','qclass','qrule','top_subject','normalization_ambiguous','conversion_anomaly_A','conversion_anomaly_embedded_B']+[k+s for k in FIELDS for s in ('_A','_embedded_B')]
 f=apply_frame(pq.read_table(p,columns=columns).to_pandas(types_mapper=arrow_type))
 for c in f.select_dtypes(include=['object','string']).columns:f[c]=f[c].astype('string[pyarrow]')
 return f

def choose(store,category,mode='latest',*,month=None,base=None,current=None):
 snaps=store.snapshots(category)
 if not snaps:return {'available':False,'reason':'Нет принятых рыночных снимков этой категории'}
 mode={'Последние два отчёта':'latest','Начало месяца → последний снимок':'month','Произвольные два снимка':'custom'}.get(mode,mode)
 if mode=='latest':a=snaps[-1];b=snaps[-2] if len(snaps)>1 else None;embedded=b is None
 elif mode=='month':
  month=month or received_month(snaps[-1]);eligible=[r for r in snaps if received_month(r)==month]
  if not eligible:return {'available':False,'reason':'Нет снимков за выбранный месяц загрузки'}
  a=eligible[-1];base=base or store.index()['monthly_baselines'][category+'|'+month]
  b=next((r for r in eligible if r['id']==base),None)
  if b is None:raise ValueError('База должна принадлежать выбранным категории и месяцу загрузки')
  embedded=False
 elif mode=='custom':
  a=next((r for r in snaps if r['id']==current),None);b=next((r for r in snaps if r['id']==base),None);embedded=False
  if a is None or b is None:raise ValueError('Оба снимка должны относиться к одной выбранной категории')
 else:raise ValueError('Неизвестный режим сравнения')
 lone=b is not None and a['id']==b['id']
 bstart=a['previous_start'] if embedded else b['start']
 bend=a['previous_end'] if embedded else b['end']
 overlap=max(a['start'],bstart)<=min(a['end'],bend)
 adays=(dt.date.fromisoformat(a['end'])-dt.date.fromisoformat(a['start'])).days+1
 bdays=(dt.date.fromisoformat(bend)-dt.date.fromisoformat(bstart)).days+1
 warnings=[]
 if embedded:warnings.append('Первый снимок: база B встроена в файл и относится только к фразам текущего отчёта. Это не полная предыдущая выборка; исторические доли и вышедшие фразы недоступны.')
 if lone:warnings.append('Пока один снимок для этого сравнения: динамика между снимками Н/Д. Это не накопленный спрос за месяц.')
 if overlap:warnings.append('Периоды частично пересекаются: это сравнение снимков, а не независимых недель.' if (a['start'],a['end'])!=(bstart,bend) else 'Периоды совпадают: сравнение версий/одного снимка, а не независимых недель.')
 if adays!=bdays:warnings.append('Разная длительность периодов: прирост не является сопоставимым недельным трендом.')
 if mode=='month' and a['start'][:7]!=month or mode=='month' and bstart[:7]!=month:warnings.append('Окно базового или последнего отчёта включает предыдущий календарный месяц.')
 return {'available':True,'category':category,'mode':mode,'a':a,'b':b,'b_start':bstart,'b_end':bend,'embedded':embedded,'single':lone,'full_structure':not embedded and not lone,'overlap':overlap,'equal_duration':adays==bdays,'independent_weeks':not overlap and adays==bdays==7,'warnings':warnings}

def comparison(store,context):
 if not context.get('available'):return pd.DataFrame()
 a=store.rows(context['a']['id']);b=a if context['embedded'] or context['single'] else store.rows(context['b']['id'])
 ambiguous=set(a.loc[a.normalization_ambiguous,'query_norm'])|set(b.loc[b.normalization_ambiguous,'query_norm'])
 def keyed(f):
  # Original XLSX columns duplicate the prepared metrics/text. Project before
  # aligning snapshots; an embedded base shares the same aligned frame.
  cols=['query_norm','query_raw','qclass','qrule','top_subject','normalization_ambiguous','conversion_anomaly_A','conversion_anomaly_embedded_B']+[k+s for k in FIELDS for s in ('_A','_embedded_B')]
  f=f[cols].copy(deep=False);f['comparison_key']=f.query_norm.where(~f.query_norm.isin(ambiguous),f.query_norm+' ␟ '+f.query_raw)
  return f.set_index('comparison_key')
 x=keyed(a);y=x if b is a else keyed(b);idx=x.index.union(y.index)
 present_a=idx.isin(x.index);present_b=idx.isin(y.index)
 if not x.index.equals(idx):x=x.reindex(idx)
 if y is not x and not y.index.equals(idx):y=y.reindex(idx)
 del a,b
 out=pd.DataFrame(index=idx)
 for col in ('query_norm','query_raw','qclass','qrule','top_subject'):
  out[col]=x[col].combine_first(y[col])
 out['category']=context['category']
 for m in FIELDS:
  now=x[m+'_A'];before=y[m+('_embedded_B' if context['embedded'] else '_A')]
  if context['single']:before=pd.Series(np.nan,index=idx)
  out[m+'_B']=before;out[m+'_A']=now
  out[m+'_delta']=now-before
  out[m+'_pct']=(now/before.where(before>0)-1)*100
  if m in LABELS:
   out[m+'_share_A']=now/now.sum()*100 if context['full_structure'] and now.sum()>0 else np.nan
   out[m+'_share_B']=before/before.sum()*100 if context['full_structure'] and before.sum()>0 else np.nan
   out[m+'_share_pp']=out[m+'_share_A']-out[m+'_share_B']
 for s in ('A','B'):
  out['cart_cr_'+s]=out['carts_'+s]/out['clicks_'+s].where(out['clicks_'+s]>0)*100
  out['order_cr_'+s]=out['orders_'+s]/out['carts_'+s].where(out['carts_'+s]>0)*100
 out['normalization_ambiguous']=x.normalization_ambiguous.astype('boolean').fillna(False)|y.normalization_ambiguous.astype('boolean').fillna(False)
 out['conversion_anomaly_A']=x['conversion_anomaly_A'].astype('boolean').fillna(False)
 out['conversion_anomaly_B']=y['conversion_anomaly_embedded_B' if context['embedded'] else 'conversion_anomaly_A'].astype('boolean').fillna(False)
 out['status']=['Один снимок — динамика Н/Д' if context['single'] else 'Новый / база 0' if not pb or by==0 else 'Вышел из доступной выборки — спрос Н/Д' if not pa else 'Обе базы доступны' for pa,pb,by in zip(present_a,present_b,out.frequency_B)]
 if context['embedded']:out['status']=np.where(out.frequency_B.eq(0),'Новый / встроенная база 0','Встроенная база — только фразы A')
 out['eligible_demand']=out.frequency_A.notna()&out.frequency_B.notna()&context['equal_duration']&(not context['single'])
 out['share_basis']='Две полные выгрузки одной категории: сумма показателя по фразам каждой выгрузки, не доля рынка O’STIN' if context['full_structure'] else 'Исторические доли Н/Д: встроенная база не полная предыдущая выборка / один снимок'
 out.index.name='phrase_key'
 out=out.reset_index()
 for c in out.select_dtypes(include=['object','string']).columns:out[c]=out[c].astype('string[pyarrow]')
 return out

def filter_rows(frame,phrase='',classes=(),subject=None,unknown_kind='Все'):
 f=frame
 if phrase:f=f[f.query_norm.str.contains(normalize(phrase),regex=False,na=False)]
 if classes:f=f[f.qclass.isin(classes)]
 if subject:f=f[f.top_subject.isin(subject)]
 if unknown_kind=='Смешанные / конфликтные':f=f[f.qrule.eq('CONFLICT_BRANDS')]
 elif unknown_kind=='Прочие неопределённые':f=f[f.qclass.eq('UNKNOWN')&~f.qrule.eq('CONFLICT_BRANDS')]
 return f

def rank(frame,kind='absolute',minimum_frequency=1000,minimum_base=100,minimum_absolute=100,minimum_pct=10):
 f=frame[frame.eligible_demand&frame.frequency_A.ge(minimum_frequency)].copy()
 if kind=='new':return frame[frame.status.str.startswith('Новый')&frame.frequency_A.ge(minimum_frequency)].sort_values('frequency_A',ascending=False)
 if kind=='exited':return frame[frame.status.str.startswith('Вышел')].sort_values('frequency_B',ascending=False)
 if kind=='fall':return f[f.frequency_delta.le(-minimum_absolute)&f.frequency_pct.le(-minimum_pct)].sort_values('frequency_delta')
 if kind=='stable':return f[f.frequency_B.ge(minimum_base)&f.frequency_pct.abs().lt(minimum_pct)].sort_values('frequency_A',ascending=False)
 if kind=='share':return f[f.frequency_share_pp.notna()&f.frequency_delta.ge(minimum_absolute)].sort_values('frequency_share_pp',ascending=False)
 if kind=='relative':return f[f.frequency_B.ge(minimum_base)&f.frequency_delta.ge(minimum_absolute)&f.frequency_pct.ge(minimum_pct)].sort_values(['frequency_pct','frequency_delta'],ascending=False)
 return f[f.frequency_delta.ge(minimum_absolute)&(f.frequency_pct.ge(minimum_pct)|f.frequency_B.eq(0))].sort_values(['frequency_delta','frequency_A'],ascending=False)

def note(row,assortment_count=None):
 a=row.frequency_A;b=row.frequency_B;delta=row.frequency_delta
 fmt=lambda v:'Н/Д' if pd.isna(v) else f'{v:,.0f}'.replace(',',' ')
 change='периоды несопоставимы' if not row.eligible_demand else f"Δ {fmt(delta)} запросов ({row.frequency_pct:.2f}%)" if pd.notna(row.frequency_pct) else 'новый / база 0'
 relevance='Проверить наличие релевантного ассортимента O’STIN' if not assortment_count else f'В исторической выборке O’STIN найдено {assortment_count} моделей того же предмета; проверить актуальный ассортимент'
 return f"Факт WB, {row.category}: {row.query_raw} — {fmt(b)} → {fmt(a)}; {change}. {relevance}. Проверить гендер, коллекцию, сезонность, карточки, размеры и поисковое присутствие. Рост частотности не является поручением увеличить рекламный бюджет."

def import_file(path,provenance=None):return Store().accept(path,provenance=provenance)
if __name__=='__main__':
 import sys
 sys.stdout.reconfigure(encoding='utf8')
 path=Path(sys.argv[1]) if len(sys.argv)>1 else next((ROOT/'market_inbox').glob('Поисковые запросы*.zip'))
 provenance=json.loads((ROOT/'MARKET_DRIVE_PROVENANCE.json').read_text(encoding='utf8')) if (ROOT/'MARKET_DRIVE_PROVENANCE.json').exists() else None
 result=import_file(path,provenance);print(json.dumps({'status':result['status'],'snapshot':result['snapshot']},ensure_ascii=False,indent=2))

_rows.cache_clear=lambda:None
