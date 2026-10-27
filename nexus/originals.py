"""Bounded read-only fetches of sources explicitly cited by published briefings."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from html.parser import HTMLParser
from .ingest import fetch,source,plain
from .model import InvalidData,instant,stamp

class Metadata(HTMLParser):
 def __init__(self):super().__init__();self.date='';self.title='';self.in_title=False
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if tag=='meta' and (a.get('property') or a.get('name')) in {'article:published_time','datePublished','date'}:self.date=a.get('content','')
  if tag=='title':self.in_title=True
 def handle_endtag(self,tag):
  if tag=='title':self.in_title=False
 def handle_data(self,data):
  if self.in_title:self.title+=data

def collect(state,now,getter=fetch,limit=24):
 parents={}
 for s in state['sources'].values():
  if s['kind']!='briefing':continue
  for url in s.get('links',[]):
   if url not in parents or s['publishedAt']<parents[url]['publishedAt']:parents[url]=s
 checks=state.setdefault('sourceChecks',{})
 due=[(url,s) for url,s in parents.items() if url not in checks or now-instant(checks[url]['at'])>=timedelta(days=7)]
 # Failed fetches rotate behind unseen URLs; every cited source eventually gets a turn.
 due.sort(key=lambda p:checks.get(p[0],{}).get('at',''))
 def one(pair):
  url,parent=pair
  try:
   text,final=getter(url);meta=Metadata();meta.feed(text)
   published=parent['publishedAt'];basis='referenced'
   try:
    if meta.date and instant(meta.date)<=now:published=meta.date;basis='published'
   except InvalidData:pass
   body=plain(text)
   if len(body)<100:raise InvalidData('No usable public text')
   row=source(final,meta.title.strip() or parent['title'],body,published,now,kind='original',origin='unspecified',briefing_id=parent.get('briefingId',''))
   row['dateBasis']=basis;row['referredFrom']=parent['id']
   return url,row,''
  except (InvalidData,ValueError,OSError) as exc:return url,None,type(exc).__name__
 rows=[];errors=[]
 with ThreadPoolExecutor(max_workers=4) as pool:
  for url,row,error in pool.map(one,due[:limit]):
   checks[url]={'at':stamp(now),'status':'failed' if error else 'ok'}
   if row:rows.append(row)
   else:errors.append({'stage':'original','url':url,'error':error})
 return rows,errors
