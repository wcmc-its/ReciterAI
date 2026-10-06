import json, time, re, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
rows=json.load(open('rows.json'))
P=sorted({x['pmid'] for x in rows})
HOME=re.compile(r'Weill|Cornell|WCM|NewYork-Presbyterian|New York-Presbyterian|NYP',re.I)
out={}
for b in range(0,len(P),150):
    q=urllib.parse.urlencode({'db':'pubmed','id':','.join(P[b:b+150]),'retmode':'xml'}).encode()
    x=urllib.request.urlopen('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi',q,timeout=120).read()
    root=ET.fromstring(x)
    for art in root.iter('PubmedArticle'):
        pmid=art.find('.//PMID').text
        auths=art.findall('.//AuthorList/Author')
        affs=[' '.join(a.text or '' for a in au.findall('.//Affiliation')) for au in auths]
        h=[bool(HOME.search(a)) for a in affs]
        out[pmid]={'n':len(h),'first':h[0] if h else False,'last':h[-1] if h else False,'frac':sum(h)/len(h) if h else 0}
    time.sleep(0.4)
json.dump(out,open('pubmed_aff.json','w')); print(len(P),len(out))
