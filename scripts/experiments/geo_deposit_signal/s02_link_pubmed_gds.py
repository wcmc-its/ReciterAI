import json, time, urllib.request, urllib.parse
r=json.load(open('core_rows.json'))
pmids=sorted({i['pmid'] for i in r})
import os
L=json.load(open('elink_gds.json')) if os.path.exists('elink_gds.json') else {}
todo=[p for p in pmids if p not in L]
has=set()
for b in range(0,len(todo),300):
    batch=todo[b:b+300]
    term='pubmed_gds[Filter] AND ('+' OR '.join(p+'[uid]' for p in batch)+')'
    data=urllib.parse.urlencode({'db':'pubmed','term':term,'retmax':1000,'retmode':'json'}).encode()
    for a in range(5):
        try: j=json.load(urllib.request.urlopen('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi',data,timeout=60)); break
        except Exception as e: print('retry',e,flush=True); time.sleep(3)
    else: raise SystemExit('fail')
    has|=set(j['esearchresult']['idlist'])
    time.sleep(0.4)
print('remaining',len(todo),'with gds',len(has),flush=True)
for p in todo:
    if p not in has: L[p]=[]
hl=sorted(has)
for b in range(0,len(hl),20):
    batch=hl[b:b+20]
    q=urllib.parse.urlencode([('dbfrom','pubmed'),('db','gds'),('retmode','json')]+[('id',p) for p in batch])
    for a in range(5):
        try: j=json.load(urllib.request.urlopen('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi?'+q,timeout=90)); break
        except Exception as e: print('retry',e,flush=True); time.sleep(3)
    else: raise SystemExit('fail elink')
    for ls in j['linksets']:
        L[ls['ids'][0]]=next((d['links'] for d in ls.get('linksetdbs',[]) if d['linkname']=='pubmed_gds'),[])
    time.sleep(0.4)
json.dump(L,open('elink_gds.json','w'))
print('done',len(L),sum(1 for v in L.values() if v))
