"""Upper-quantile cost model: trained on signed settlements, validated in mould groups."""
import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import GroupKFold
from sklearn.metrics import mean_pinball_loss
from sqlalchemy import select
from .models import Mould,Estimate
from .execution_models import TrialExecution,CostReview
QUANTILE=0.85
MIN_SAMPLES=30
MIN_GROUPS=4
STEEL={'P20':0,'H13':1,'S136':2}
FEATURES=['模腔','滑块','重量kg','钢材','费率','预测机时']
def load_rows(s):
    q=(select(CostReview,TrialExecution,Estimate,Mould)
       .join(TrialExecution,CostReview.execution_id==TrialExecution.id)
       .join(Estimate,TrialExecution.estimate_id==Estimate.id)
       .join(Mould,Estimate.mould_id==Mould.id)
       .where(CostReview.status=='signed'))
    return s.execute(q).all()
def vector(mould,rate,hours):return [mould.cavities,mould.slides,mould.weight_kg,STEEL[mould.steel],rate,hours]
def build(rows):
    model=GradientBoostingRegressor(loss='quantile',alpha=QUANTILE,random_state=42)
    x=np.array([vector(m,xr.snapshot['rate'],xr.snapshot['predicted_hours']) for r,xr,e,m in rows])
    y=np.array([r.actual_cost for r,xr,e,m in rows])
    return model,x,y
def train(s):
    rows=load_rows(s)
    if len(rows)<MIN_SAMPLES:raise ValueError(f'已签结算样本 {len(rows)} 条，不足 {MIN_SAMPLES} 条，成本上分位模型不可信，暂缓预算承诺')
    groups=sorted({e.mould_id for r,x,e,m in rows})
    if len(groups)<MIN_GROUPS:raise ValueError(f'签结样本只覆盖 {len(groups)} 套模具，不足 {MIN_GROUPS} 组，分组验证不可靠，暂缓预算承诺')
    model,x,y=build(rows)
    g=np.array([e.mould_id for r,xr,e,m in rows])
    cv=GroupKFold(n_splits=min(5,len(groups)))
    coverage,pinball=[],[]
    for tr,te in cv.split(x,y,g):
        fold,_,_=build(rows)
        fold.fit(x[tr],y[tr])
        p=fold.predict(x[te])
        coverage.append(float(np.mean(y[te]<=p)))
        pinball.append(float(mean_pinball_loss(y[te],p,alpha=QUANTILE)))
    model.fit(x,y)
    evidence={'model':'GradientBoosting-quantile','quantile':QUANTILE,'samples':len(rows),'mould_groups':len(groups),'coverage':round(float(np.mean(coverage)),3),'pinball_loss':round(float(np.mean(pinball)),2),'folds':len(coverage),'seed':42,'features':FEATURES,'note':'按模具分组交叉验证，整组留出防泄漏；覆盖率=留出组实际成本不超过预测上分位的比例，目标不低于分位水平。'}
    return model,evidence
def predict_upper(model,mould,rate,hours):
    return max(0.0,float(model.predict([vector(mould,rate,hours)])[0]))
