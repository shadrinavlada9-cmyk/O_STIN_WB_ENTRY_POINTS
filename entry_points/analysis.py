"""SQL selection over two Entry Points weeks, accepted comparisons on small results."""
import json,contextlib,threading
import pandas as pd
from . import accepted as A
from .classifier import normalize
from .dataset import connect
KEYS=('gender','collection','product_type','seller_article','nm_id','cm')
METRICS=['clicks','carts','orders','impressions']
GATE=threading.RLock()
def where(filters,alias='p'):
 clauses=[];params=[]
 for key in KEYS:
  values=filters.get(key)
  if values:
   field=('CAST('+alias+'.nm_id AS TEXT)') if key=='nm_id' else alias+'.'+key
   clauses.append(field+' IN(SELECT value FROM json_each(?))');params.append(json.dumps([str(v) for v in values]))
 return (' AND '+' AND '.join(clauses)) if clauses else '',params
class Session:
 def __init__(self,feed,index,a,b,filters=None):
  self.feed=feed;self.index=index;self.a=a;self.b=b;self.filters=filters or {};self.records={r['week']:r for r in index['weeks']};self.connections={};self.scope=None
 def __enter__(self):
  GATE.acquire()
  try:return self._open()
  except BaseException:
   for con in self.connections.values():con.close()
   GATE.release();raise
 def _open(self):
  for week in dict.fromkeys([self.a,self.b]):self.connections[week]=connect(self.feed.database(self.records[week]))
  active=bool(self.filters.get('qclass') or self.filters.get('phrase') or self.filters.get('unknown_kind','Все')!='Все')
  if active:
   ids=set();cohort,params=where(self.filters)
   for con in self.connections.values():
    extra='';args=list(params)
    if self.filters.get('qclass'):extra+=' AND q.qclass IN(SELECT value FROM json_each(?))';args.append(json.dumps(self.filters['qclass']))
    if self.filters.get('phrase'):extra+=' AND instr(q.query_norm,?)>0';args.append(normalize(self.filters['phrase']))
    kind=self.filters.get('unknown_kind','Все')
    if kind=='Смешанные / конфликтные':extra+=" AND q.qrule='CONFLICT_BRANDS'"
    elif kind!='Все':extra+=" AND q.qclass='UNKNOWN' AND q.qrule!='CONFLICT_BRANDS'"
    ids.update(row[0] for row in con.execute('SELECT DISTINCT p.nm_id FROM query_filters qf JOIN queries q ON q.id=qf.query_id JOIN products p ON p.id=qf.product_id WHERE 1'+cohort+extra,args))
   self.scope=json.dumps(sorted(ids))
  return self
 def __exit__(self,*args):
  try:
   for con in self.connections.values():con.close()
  finally:GATE.release()
 def select(self,detail=None,*,denominator=False):
  frames=[];cohort,params=where(self.filters)
  for week,con in self.connections.items():
   predicate=cohort;args=list(params)
   if not denominator and self.scope is not None:predicate+=' AND p.nm_id IN(SELECT value FROM json_each(?))';args.append(self.scope)
   if detail:
    predicate+=' AND e.section=? AND e.entry_point=?';args.extend(detail)
   keys=['p.seller_article','p.nm_id','e.section','e.entry_point'] if detail else ['e.section','e.entry_point']
   # A grouped source summary is a sequential scan, not hundreds of thousands
   # of non-covering ep_pair index lookups. Detail uses the selective pair index.
   access='' if detail else ' NOT INDEXED'
   sql='SELECT '+','.join(keys)+','+','.join('SUM(e.'+m+') AS '+m for m in METRICS)+',MAX(e.row_anomaly) AS row_anomaly FROM entries e'+access+' JOIN products p ON p.id=e.product_id WHERE 1'+predicate+' GROUP BY '+','.join(keys)+' ORDER BY MIN(e.ordinal)'
   f=pd.read_sql_query(sql,con,params=args);f['week']=week;frames.append(f)
  out=pd.concat(frames,ignore_index=True);out['row_anomaly']=out.row_anomaly.astype(bool);return out
 def flags(self):
  out=[]
  for week,con in self.connections.items():
   f=pd.read_sql_query('SELECT * FROM flags',con);f['week']=week;f.anomaly=f.anomaly.astype(bool);out.append(f)
  return pd.concat(out,ignore_index=True)
 def summary(self):
  denominator=self.select(denominator=True);visible=self.select();flags=self.flags();token=A.FLAGS.set(flags)
  try:f=A.fixed_shares(A.entry_table(visible,self.a,self.b),denominator,self.a,self.b)
  finally:A.FLAGS.reset(token)
  if self.filters.get('entry'):f=f[(f['Раздел']+' · '+f['Точка входа']).isin(self.filters['entry'])]
  return f
 def detail(self,section,point):
  e=self.select((section,point));token=A.FLAGS.set(self.flags())
  try:return A.entry_table(e,self.a,self.b,['seller_article','nm_id'])
  finally:A.FLAGS.reset(token)
 def diagnostics(self):
  rows=[];cohort,params=where(self.filters)
  for week,con in self.connections.items():
   result=con.execute('SELECT COUNT(*),SUM(e.clicks),SUM(e.carts),SUM(e.orders),SUM(e.impressions) FROM entries e JOIN products p ON p.id=e.product_id WHERE 1'+cohort,params).fetchone()
   rows.append({'Неделя':week,'Строк источника':result[0],'Переходы':result[1],'Корзины':result[2],'Заказы':result[3],'Показы':result[4]})
  return pd.DataFrame(rows)
 def anomaly_impact(self):
  rows=[];cohort,params=where(self.filters)
  for week,con in self.connections.items():
   count,clicks,carts,orders=con.execute('SELECT COUNT(*),SUM(e.clicks),SUM(e.carts),SUM(e.orders) FROM anomalies e JOIN products p ON p.id=e.product_id WHERE 1'+cohort,params).fetchone()
   if count:rows.append({'source':'Портрет','week':week,'clicks':clicks,'carts':carts,'orders':orders,'Аномальных строк':count})
  return pd.DataFrame(rows,columns=['source','week','clicks','carts','orders','Аномальных строк'])
 def anomaly_rows(self):
  frames=[];cohort,params=where(self.filters)
  for week,con in self.connections.items():
   sql='SELECT p.nm_id,p.seller_article,p.cm,p.gender,p.collection,p.product_type,e.section,e.entry_point,e.clicks,e.carts,e.orders,e.impressions,e.source_ctr,e.source_cart_cr,e.source_order_cr,e.reason AS anomaly_reason FROM anomalies e JOIN products p ON p.id=e.product_id WHERE 1'+cohort+' ORDER BY e.ordinal'
   f=pd.read_sql_query(sql,con,params=params);f['week']=week;f['source']='Портрет';frames.append(f)
  return pd.concat(frames,ignore_index=True)
