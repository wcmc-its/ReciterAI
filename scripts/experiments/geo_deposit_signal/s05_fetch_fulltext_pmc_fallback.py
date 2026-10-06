import json, os, time, urllib.request, glob
meta=json.load(open('epmc_meta.json'))
n=ok=0
for e in glob.glob('ft/*.xml.err'):
    p=os.path.basename(e)[:-8]; pmc=meta[p]['pmcid']
    url=f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id={pmc[3:]}&retmode=xml'
    try: x=urllib.request.urlopen(url,timeout=60).read()
    except Exception as ex: print('fail',p,ex); continue
    n+=1
    if b'<body' in x:
        open(f'ft/{p}.xml','wb').write(x); os.remove(e); ok+=1
    time.sleep(0.4)
print('tried',n,'got body',ok)
