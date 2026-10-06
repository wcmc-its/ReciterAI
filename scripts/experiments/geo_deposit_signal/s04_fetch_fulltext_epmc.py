import json, time, os, urllib.request, urllib.parse
r=json.load(open('core_rows.json')); L=json.load(open('elink_gds.json'))
U=sorted({i['pmid'] for i in r if i['core_id'] in ('1','3','5') and L.get(i['pmid'])})
print('universe',len(U),flush=True)
meta=json.load(open('epmc_meta.json')) if os.path.exists('epmc_meta.json') else {}
todo=[p for p in U if p not in meta]
for b in range(0,len(todo),50):
    batch=todo[b:b+50]
    q='SRC:MED AND ('+' OR '.join('EXT_ID:'+p for p in batch)+')'
    url='https://www.ebi.ac.uk/europepmc/webservices/rest/search?'+urllib.parse.urlencode({'query':q,'format':'json','pageSize':100,'resultType':'lite'})
    j=json.load(urllib.request.urlopen(url,timeout=60))
    for x in j['resultList']['result']:
        meta[x['pmid']]={'pmcid':x.get('pmcid'),'inEPMC':x.get('inEPMC'),'oa':x.get('isOpenAccess')}
    for p in batch: meta.setdefault(p,{})
    time.sleep(0.2)
json.dump(meta,open('epmc_meta.json','w'))
print('with pmcid+inEPMC',sum(1 for v in meta.values() if v.get('pmcid') and v.get('inEPMC')=='Y'),flush=True)
for p,v in meta.items():
    if not (v.get('pmcid') and v.get('inEPMC')=='Y'): continue
    fn=f'ft/{p}.xml'
    if os.path.exists(fn): continue
    try:
        x=urllib.request.urlopen(f"https://www.ebi.ac.uk/europepmc/webservices/rest/{v['pmcid']}/fullTextXML",timeout=60).read()
        open(fn,'wb').write(x)
    except Exception as e:
        open(fn+'.err','w').write(str(e))
    time.sleep(0.15)
print('done',len(os.listdir('ft')))
