import json, re, os, random, math, collections, sys
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from classify import facility_hits
r=json.load(open('core_rows.json')); L=json.load(open('elink_gds.json')); G=json.load(open('geo_text.json'))
SKIP=re.compile(r'^!(Series|Sample)_(contact_\w+|sample_id|contributor|relation|supplementary_file\w*|characteristics\w*|platform\w*|geo_accession|status|\w+_date|pubmed_id|series_id|title|type)\b')
def geo_parts(gse):
    v=G.get(gse); 
    if not v: return None
    lines=(v['series']+'\n'+v['gsm']).splitlines()
    proto='\n'.join(l for l in lines if l.startswith('!') and not SKIP.match(l))
    inst=' '.join(l.split(' = ',1)[1] for l in lines if '_contact_institute' in l or '_contact_department' in l or '_contact_laboratory' in l)
    instr={l.split(' = ',1)[1].strip() for l in lines if 'instrument_model' in l}
    return proto, inst, instr
HOME=re.compile(r'Weill|Cornell|WCM|WCMC',re.I)
def geo_feats(pmid):
    f=collections.Counter(); instr=set(); n=0
    for u in L.get(pmid,[]):
        if not (u.startswith('200') and len(u)==9): continue
        p=geo_parts('GSE%d'%int(u[3:]))
        if p is None: continue
        n+=1; proto,inst,ins=p; instr|=ins
        for t in facility_hits(proto): f['g_'+t]+=1
        if HOME.search(inst): f['g_contact_home']+=1
    f['n_gse']=n
    return f, instr
def strip(x):
    # label text = body + back matter only; drop front matter, affiliations, author lists, refs
    x=re.sub(r'(?s)<front>.*?</front>','',x)
    x=re.sub(r'(?s)<(aff|contrib-group|ref-list)[^>]*>.*?</\1>','',x)
    return re.sub(r'<[^>]+>',' ',x)
FT={}
def fulltext(pmid):
    if pmid in FT: return FT[pmid]
    fn=f'ft/{pmid}.xml'
    FT[pmid]=strip(open(fn,encoding='utf-8',errors='replace').read()) if os.path.exists(fn) else None
    return FT[pmid]
def core_tag(tags, core):
    if core=='5': return bool({'core5','home_generic'}&tags)
    if core=='3': return 'core3' in tags
    if core=='1': return 'core1' in tags
rows=[]
for i in r:
    c=i['core_id']
    if c not in ('1','3','5') or not L.get(i['pmid']): continue
    gf,instr=geo_feats(i['pmid'])
    if gf['n_gse']==0: continue
    ft=fulltext(i['pmid'])
    ftt=facility_hits(ft) if ft else None
    gt={k[2:] for k in gf if k.startswith('g_') and gf[k]}
    rows.append(dict(pmid=i['pmid'],core=c,status=i['status'],staff=bool(i.get('signal_coauthors')) and i['status']=='confirmed',
        llm=float(i['screen_confidence']) if i.get('screen_confidence') else None,
        aff=float(i.get('author_affinity') or 0),
        has_ft=ft is not None, ack=core_tag(ftt,c) if ftt is not None else None,
        ft_external='external' in ftt if ftt is not None else None,
        g_core=core_tag(gt,c), g_external='external' in gt, g_home='contact_home' in gt,
        g_any_wcm_core=bool({'core5','core3','home_generic','core1'}&gt), instr=sorted(instr)))
json.dump(rows,open('rows.json','w'))
print('rows',len(rows))
