import json, math, random
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import LeaveOneOut, cross_val_predict
rows=json.load(open('rows.json'))
def wilson(k,n,z=1.96):
    if n==0: return (float('nan'),)*3
    p=k/n; d=1+z*z/n; c=(p+z*z/(2*n))/d; h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return p,c-h,c+h
def fmt(k,n): p,lo,hi=wilson(k,n); return f'{k}/{n} = {p:.2f} [{lo:.2f},{hi:.2f}]'
for core in ('5','3','1'):
    R=[x for x in rows if x['core']==core]
    print(f'\n=== core {core}: {len(R)} GEO-linked rows; with full text {sum(x["has_ft"] for x in R)}; staff-confirmed {sum(x["staff"] for x in R)}')
    lab=[x for x in R if x['has_ft']]
    pos=lambda x: x['ack'] or x['staff']
    for feat in ('g_core','g_any_wcm_core','g_external','g_home'):
        on=[x for x in lab if x[feat]]; off=[x for x in lab if not x[feat]]
        print(f'  {feat:15s} fires {len(on)}: P(paper-ack|staff | on) {fmt(sum(map(pos,on)),len(on))}  | off {fmt(sum(map(pos,off)),len(off))}')
    # incremental: g_core fires, no paper ack, not staff
    inc=[x['pmid'] for x in R if x['g_core'] and not x['staff'] and not x['ack']]
    print('  g_core fires w/o paper-ack & w/o staff:', len(inc), inc[:30])
    # AUC among candidates with LLM score, label = paper-text ack
    C=[x for x in lab if x['status']=='candidate' and x['llm'] is not None]
    y=np.array([int(bool(x['ack'])) for x in C])
    print(f'  candidate AUC panel n={len(C)} pos={y.sum()}')
    if y.sum()<3 or y.sum()==len(y): continue
    def X(feats): return np.array([[x['llm'],x['aff']]+[float(x[f]) for f in feats] for x in C])
    def loo(feats):
        return cross_val_predict(LogisticRegression(C=1.0,max_iter=1000),X(feats),y,cv=LeaveOneOut(),method='predict_proba')[:,1]
    base=loo([]); 
    for feats in (['g_core'],['g_external'],['g_home'],['g_home','g_core','g_external']):
        p=loo(feats)
        a0,a1=roc_auc_score(y,base),roc_auc_score(y,p)
        rng=np.random.default_rng(0); ds=[]
        for _ in range(2000):
            i=rng.integers(0,len(y),len(y))
            if y[i].sum() in (0,len(i)): continue
            ds.append(roc_auc_score(y[i],p[i])-roc_auc_score(y[i],base[i]))
        lo,hi=np.percentile(ds,[2.5,97.5])
        print(f'   LOO AUC base(llm+aff)={a0:.3f}  +{feats}={a1:.3f}  dAUC={a1-a0:+.3f} [{lo:+.3f},{hi:+.3f}]')
        rep=np.array([x['aff']>0 for x in C])
        if y[rep].sum()>=3 and y[rep].sum()<rep.sum():
            print(f'     repeat-user subset n={rep.sum()} pos={y[rep].sum()}: base {roc_auc_score(y[rep],base[rep]):.3f} -> {roc_auc_score(y[rep],p[rep]):.3f}')
