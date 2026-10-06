import json, time, os, re, urllib.request, threading
from concurrent.futures import ThreadPoolExecutor
r=json.load(open('core_rows.json')); L=json.load(open('elink_gds.json'))
P={i['pmid'] for i in r if i['core_id'] in ('1','3','5')}
uids=sorted({u for p in P for u in L.get(p,[]) if u.startswith('200') and len(u)==9})
gses=['GSE%d'%int(u[3:]) for u in uids]
import os
out=json.load(open('geo_text.json')) if os.path.exists('geo_text.json') else {}
todo=[g for g in gses if g not in out]
print('target',len(gses),'todo',len(todo),flush=True)
lock=threading.Lock()
def get(acc):
    url=f'https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={acc}&targ=self&form=text&view=brief'
    for a in range(4):
        try: return urllib.request.urlopen(url,timeout=60).read().decode('utf-8','replace')
        except Exception: time.sleep(3*(a+1))
    return None
def work(g):
    s=get(g); time.sleep(0.5)
    if s is None: return
    m=re.search(r'!Series_sample_id = (GSM\d+)',s)
    gsm=get(m.group(1)) if m else ''
    time.sleep(0.5)
    with lock: out[g]={'series':s,'gsm':gsm or ''}
with ThreadPoolExecutor(3) as ex:
    for k,_ in enumerate(ex.map(work,todo)):
        if k%100==0:
            with lock: json.dump(out,open('geo_text.json','w'))
            print(k,flush=True)
json.dump(out,open('geo_text.json','w')); print('done',len(out),flush=True)
