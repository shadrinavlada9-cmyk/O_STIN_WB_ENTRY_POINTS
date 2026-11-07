"""A strict byte budget for small immutable JSON metadata, never DataFrames."""
from collections import OrderedDict
import json,threading
class Cache:
 def __init__(self,budget=2*1024**2):self.budget=budget;self.used=0;self.items=OrderedDict();self.lock=threading.RLock()
 def clear(self):
  with self.lock:self.items.clear();self.used=0
 def get(self,key,build):
  with self.lock:
   if key in self.items:
    blob=self.items.pop(key);self.items[key]=blob;return json.loads(blob)
   value=build();blob=json.dumps(value,ensure_ascii=False).encode('utf8')
   if len(blob)<=self.budget:
    while self.used+len(blob)>self.budget:
     _,old=self.items.popitem(last=False);self.used-=len(old)
    self.items[key]=blob;self.used+=len(blob)
   return value
