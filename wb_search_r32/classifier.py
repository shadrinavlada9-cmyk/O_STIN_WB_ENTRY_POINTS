"""Deterministic query text classification; never classifies product ownership."""
import re,unicodedata
VERSION='WB_QUERY_R3_3'
ALIASES=('остин','ostin','o stin','о стин') # confirmed B2 and actual W40–43
OTHER=('funday','fun day','фандей','фандэй','befree','бифри','sela','села','zara','зара','gloria jeans','глория джинс','lime','лайм','love republic','incity','инсити','uniqlo','юникло','h m','hm','mango','манго','reserved','резервед','oodji','оджи','zolla','золла','твое','tom tailor','finn flare','acoola')
# R3.1 additions verified against literal phrases in the WB Clothing sample.
# O'STIN aliases, normalization and boundary rules remain unchanged.
OTHER_ADDITIONS=('zarina','зарина','adidas','адидас','nike','найк','puma','пума','baon','баон','lacoste','лакост','tvoe','crockid','lc waikiki','playtoday')
OTHER=OTHER+OTHER_ADDITIONS
def normalize(text):
 s=unicodedata.normalize('NFKC',str(text or '')).casefold().replace('ё','е')
 s=s.translate(str.maketrans({c:' ' for c in "'’‘`ʼʻ"}))
 return ' '.join(re.sub(r'[^\w]+',' ',s,flags=re.UNICODE).replace('_',' ').split())
BRAND=re.compile(r'(?<!\w)(?:остин|ostin|o\s+stin|о\s+стин)(?!\w)')
COMPETITOR=re.compile(r'(?<!\w)(?:'+'|'.join(re.escape(x) for x in sorted(OTHER,key=len,reverse=True))+r')(?!\w)')
def classify(text):
 n=normalize(text);b=BRAND.search(n);c=COMPETITOR.search(n)
 if b and c:return n,'UNKNOWN','CONFLICT_BRANDS'
 if b:return n,('BRAND_PURE' if not BRAND.sub('',n).strip() else 'BRAND_CATEGORY'),'EXACT_OSTIN_WORD_BOUNDARY'
 if c:return n,'COMPETITOR','EXACT_COMPETITOR:'+c.group()
 if not n or len(n)<3 or n.isdigit() or re.fullmatch(r'[a-z]*\d+[a-z0-9 ]*',n):return n,'UNKNOWN','EMPTY_SHORT_OR_ARTICLE'
 # An unreviewed Latin name may be a brand. Do not label it category demand
 # merely because our finite competitor dictionary does not contain it.
 category_tokens={'v','u','xs','s','m','l','xl','xxl','xxxl','mom','fit'}
 if any(t not in category_tokens for t in re.findall(r'[a-z]+',n)):return n,'UNKNOWN','UNREVIEWED_LATIN_TOKEN'
 return n,'NON_BRAND','NO_KNOWN_BRAND_TOKEN'
LABELS={'BRAND_PURE':'Чистый бренд O’STIN','BRAND_CATEGORY':'O’STIN + категория/уточнение','NON_BRAND':'Небрендовые товарные','COMPETITOR':'Чужой бренд','UNKNOWN':'Неопределённые'}

def apply_frame(frame):
 """Versioned derived classification; original snapshot/file is never rewritten."""
 import pandas as pd
 import pyarrow as pa
 import pyarrow.compute as pc
 out=frame.copy(deep=False);text=out.query_raw.fillna('').astype('string[pyarrow]')
 encoded=pc.dictionary_encode(text.array.__arrow_array__().combine_chunks())
 unique=[classify(v) for v in encoded.dictionary.to_pylist()]
 if 'qclass' in out and 'qclass_snapshot' not in out:out['qclass_snapshot']=out.qclass
 if 'qrule' in out and 'qrule_snapshot' not in out:out['qrule_snapshot']=out.qrule
 for i,name in enumerate(('query_norm','qclass','qrule')):
  mapped=pc.take(pa.array([v[i] for v in unique],type=pa.large_string()),encoded.indices)
  out[name]=pd.Series(pd.arrays.ArrowStringArray(mapped),index=out.index)
 out['active_classifier_version']=VERSION
 return out
