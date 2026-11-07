"""Standalone accepted XLSX primitives only; RAW import belongs to the private weekly workflow."""
import json,uuid,os
import pandas as pd
def Workbook(*args,**kwargs):
 from .search_workbook import Workbook as AcceptedWorkbook
 return AcceptedWorkbook(*args,**kwargs)
def atomic(path,obj):
 p=path.with_suffix('.'+uuid.uuid4().hex+'.tmp');p.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str),encoding='utf8');os.replace(p,path)

def number(s):return pd.to_numeric(s.astype(str).str.replace('\u00a0','').str.replace(' ','').str.replace(',','.'),errors='coerce')
