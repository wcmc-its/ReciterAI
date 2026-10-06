import json, numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import LeaveOneOut, cross_val_predict
rows=json.load(open('rows.json')); A=json.load(open('pubmed_aff.json'))
for x in rows:
    a=A.get(x['pmid'],{}); x['wcm_first']=float(a.get('first',0)); x['wcm_last']=float(a.get('last',0)); x['wcm_frac']=a.get('frac',0)
def run(C,y,basef,addf,label):
    X=lambda f: np.array([[float(x[k]) for k in f] for x in C])
    loo=lambda f: cross_val_predict(LogisticRegression(max_iter=2000),X(f),y,cv=LeaveOneOut(),method='predict_proba')[:,1]
    b=loo(basef); p=loo(basef+addf)
    rng=np.random.default_rng(0); ds=[]
    for _ in range(2000):
        i=rng.integers(0,len(y),len(y))
        if y[i].sum() in (0,len(i)): continue
        ds.append(roc_auc_score(y[i],p[i])-roc_auc_score(y[i],b[i]))
    lo,hi=np.percentile(ds,[2.5,97.5])
    rep=np.array([x['aff']>0 for x in C])
    print(f'  {label}: base {roc_auc_score(y,b):.3f} -> {roc_auc_score(y,p):.3f}  dAUC {roc_auc_score(y,p)-roc_auc_score(y,b):+.3f} [{lo:+.3f},{hi:+.3f}] | repeat-users n={rep.sum()} pos={y[rep].sum()}: {roc_auc_score(y[rep],b[rep]):.3f} -> {roc_auc_score(y[rep],p[rep]):.3f}')
for core in ('5','3'):
    C=[x for x in rows if x['core']==core and x['has_ft'] and x['status']=='candidate' and x['llm'] is not None]
    y=np.array([int(bool(x['ack'])) for x in C])
    print(f'core {core}: candidate panel n={len(C)} pos={y.sum()}')
    B0=['llm','aff']; B1=B0+['wcm_first','wcm_last','wcm_frac']
    run(C,y,B0,['wcm_first','wcm_last','wcm_frac'],'PubMed WCM-authorship over llm+aff')
    run(C,y,B1,['g_home'],'g_home over llm+aff+authorship')
    run(C,y,B1,['g_core'],'g_core over llm+aff+authorship')
    run(C,y,B1,['g_external'],'g_external over llm+aff+authorship')
    run(C,y,B1,['g_home','g_core','g_external'],'all GEO over llm+aff+authorship')
