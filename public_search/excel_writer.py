"""One checked XLSX writer for Search and Entry Points; never truncates rows."""
from pathlib import Path
from public_search.temporary import directory as export_directory
import tempfile,re,math
import pandas as pd
import numpy as np
MAX_ROWS=1048576
MAX_DATA_ROWS=MAX_ROWS-1
MAX_COLUMNS=16384
def checked(status):
 if status not in (0,None):raise RuntimeError('Ошибка записи Excel; строки не были выгружены полностью. Используйте полный CSV/ZIP.')
def render(sheets,label,kind,*,audit=None):
 import xlsxwriter
 with export_directory() as tmp:
  output=Path(tmp)/'export.xlsx'
  with xlsxwriter.Workbook(output,{'constant_memory':True,'strings_to_formulas':False,'strings_to_urls':False,'tmpdir':tmp}) as book:
   head=book.add_format({'bold':True,'bg_color':'#eef1f6','text_wrap':True,'valign':'top'});blank=book.add_format({});formats={};used=set()
   def sheet_name(name,part):
    base=re.sub(r'[\[\]:*?/\\]',' ',str(name)).strip("'") or 'Таблица'
    suffix='' if part==1 else '__'+str(part);candidate=base[:31-len(suffix)]+suffix;counter=0
    while candidate.casefold() in used:
     suffix='_'+str(len(used)) if counter==0 else '_'+str(len(used))+'_'+str(counter)
     candidate=base[:26]+suffix if len(suffix)<=5 else base[:31-len(suffix)]+suffix;counter+=1
    used.add(candidate.casefold());return candidate
   for name,frame in sheets.items():
    if callable(frame):frame=frame()
    columns=list(frame);expected=len(frame)
    if len(columns)>MAX_COLUMNS:raise ValueError('Превышен лимит колонок Excel. Используйте полный CSV/ZIP.')
    specs=[kind(c) for c in columns];part=0;written=0;part_rows=0;worksheet=None;parts=[]
    def finish():
     if worksheet is None:return
     worksheet.freeze_panes(1,1);worksheet.autofilter(0,0,part_rows,max(0,len(columns)-1));checked(worksheet.set_column(0,0,43))
     checked(worksheet.set_column(1,max(1,len(columns)-1),21))
     parts.append({'sheet':worksheet.name,'data_rows':part_rows,'autofilter_last_zero_based_row':part_rows})
    def start():
     nonlocal part,worksheet,part_rows
     finish();part+=1;part_rows=0;worksheet=book.add_worksheet(sheet_name(name,part));checked(worksheet.write_row(0,0,[label(c) for c in columns] or [''],head));checked(worksheet.set_row(0,36))
    start()
    for row in frame.itertuples(index=False,name=None):
     if len(row)!=len(columns):raise ValueError('Состав строки Excel не совпадает с заголовком; используйте полный CSV/ZIP.')
     if part_rows==MAX_DATA_ROWS:start()
     row_index=part_rows+1;nonempty=False
     for j,(c,v,spec) in enumerate(zip(columns,row,specs)):
      if v is None or v is pd.NA or isinstance(v,(float,np.floating)) and not math.isfinite(v):continue
      nonempty=True
      if isinstance(v,(bool,np.bool_)):checked(worksheet.write_string(row_index,j,'Да' if v else 'Нет'));continue
      if isinstance(v,np.generic):v=v.item()
      if isinstance(v,(int,float)):
       change,pp,pct=spec;magnitude=abs(v)
       # Construct a format once per magnitude/sign/spec, not per numeric
       # cell. Preserve the accepted literal-space Excel number formats.
       groups=0 if magnitude<1000 else 1 if magnitude<1000000 else max(1,(len(str(int(magnitude)))-1)//3)
       sign=1 if change and v>0 else -1 if change and v<0 else 0;key=(spec,groups,sign)
       if key not in formats:
        base=('# '+('### '*(groups-1))+'##0') if groups else '0'
        if pp or pct:base+='.00'
        unit='" п.п."' if pp else '"%"' if pct else ''
        fmt=('"+"' if change else '')+base+unit+';"−"'+base+unit+';'+base+unit
        color='#24784b' if sign>0 else '#a84949' if sign<0 else '#202632'
        formats[key]=book.add_format({'num_format':fmt,'font_color':color,'align':'right','font_name':'Consolas'})
       checked(worksheet.write_number(row_index,j,v,formats[key]))
      else:checked(worksheet.write_string(row_index,j,str(v)))
     # Even an all-null source record occupies a real worksheet row.
     if not nonempty:checked(worksheet.write_blank(row_index,0,None,blank))
     part_rows+=1;written+=1
    finish()
    if written!=expected:raise ValueError('Количество записанных строк Excel не совпало с источником. Используйте полный CSV/ZIP.')
    if sum(p['data_rows'] for p in parts)!=written:raise RuntimeError('Неполная разбивка Excel на листы')
    if audit is not None:audit.append({'table':name,'expected':expected,'written':written,'parts':parts})
  return output.read_bytes()
