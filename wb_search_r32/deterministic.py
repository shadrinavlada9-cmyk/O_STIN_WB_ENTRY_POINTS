"""Locale-independent tie resolution; metric, grain and NULL semantics unchanged."""
import unicodedata
import pandas as pd
POLICY='ORDERS_DESC_NFC_RAW_ASC_V1'
def key(value):
 if pd.isna(value):return None
 return unicodedata.normalize('NFC',str(value))
def leading_queries(q,week):
 f=q.loc[q.week==week,['nm_id','orders','query_raw']].copy()
 f['_nfc']=f.query_raw.map(key);f['_raw']=f.query_raw.map(lambda x:None if pd.isna(x) else str(x))
 f=f.sort_values(['nm_id','orders','_nfc','_raw'],ascending=[True,False,True,True],na_position='last',kind='stable')
 return f.groupby('nm_id').query_raw.agg(lambda x:'; '.join(map(str,x.head(3))))
def leading_entries(e,week):
 f=e.loc[e.week==week,['nm_id','orders','section','entry_point']].copy()
 for c in ('section','entry_point'):f['_'+c]=f[c].map(key);f['_raw_'+c]=f[c].map(lambda x:None if pd.isna(x) else str(x))
 f=f.sort_values(['nm_id','orders','_section','_entry_point','_raw_section','_raw_entry_point'],ascending=[True,False,True,True,True,True],na_position='last',kind='stable')
 f['entry']=f.section+' · '+f.entry_point
 return f.groupby('nm_id').entry.first()
