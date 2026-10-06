import csv, json, time, re, urllib.request, urllib.parse, numpy as np
import xml.etree.ElementTree as ET
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import LeaveOneOut, cross_val_predict
from pathlib import Path
GT=Path('/Users/paulalbert/Dropbox/Projects/Inferring Cores and Services/analysis')  # same GROUND_TRUTH as scripts/fit_evidence_weights.py
lab=list(csv.DictReader(open(GT/'labeled_set.csv'))); llm=json.load(open(GT/'calibration_llm_results.json'))
HOME=re.compile(r'Weill|Cornell|WCM|NewYork-Presbyterian|New York-Presbyterian|NYP',re.I)
P=[r['pmid'] for r in lab]; A={}
for b in range(0,len(P),150):
    q=urllib.parse.urlencode({'db':'pubmed','id':','.join(P[b:b+150]),'retmode':'xml'}).encode()
    root=ET.fromstring(urllib.request.urlopen('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi',q,timeout=120).read())
    for art in root.iter('PubmedArticle'):
        pm=art.find('.//PMID').text
        h=[bool(HOME.search(' '.join(a.text or '' for a in au.findall('.//Affiliation')))) for au in art.findall('.//AuthorList/Author')]
        A[pm]=dict(first=float(h[0]) if h else 0.,last=float(h[-1]) if h else 0.,frac=sum(h)/len(h) if h else 0.)
    time.sleep(0.4)
R=[r for r in lab if r['pmid'] in A and r['pmid'] in llm]
y=np.array([r['label']=='yes' for r in R]).astype(int)
print('panel B n',len(R),'yes',y.sum())
def X(f): return np.array([[ (llm[r['pmid']]['score'] if k=='llm' else A[r['pmid']][k]) for k in f] for r in R],float)
loo=lambda f: cross_val_predict(LogisticRegression(max_iter=2000),X(f),y,cv=LeaveOneOut(),method='predict_proba')[:,1]
b=loo(['llm']); p=loo(['llm','first','last','frac'])
rng=np.random.default_rng(0); ds=[]
for _ in range(2000):
    i=rng.integers(0,len(y),len(y)); ds.append(roc_auc_score(y[i],p[i])-roc_auc_score(y[i],b[i]))
print(f'LOO AUC llm {roc_auc_score(y,b):.3f} -> +authorship {roc_auc_score(y,p):.3f} dAUC [{np.percentile(ds,2.5):+.3f},{np.percentile(ds,97.5):+.3f}]')
for k in ('first','last'):
    on=[r for r in R if A[r['pmid']][k]]; print(k,'WCM: yes-rate',sum(r['label']=='yes' for r in on),'/',len(on),' | not WCM:',sum(r['label']=='yes' for r in R if not A[r['pmid']][k]),'/',len(R)-len(on))
