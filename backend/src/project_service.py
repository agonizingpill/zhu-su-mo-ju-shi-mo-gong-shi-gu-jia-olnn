"""Budget commitment, release and settlement run in one transaction with row locks.

Lock order is always estimate -> project -> slot so concurrent commits, cancels
and settlements serialize instead of deadlocking. Ledger request_ids are
deterministic per hold lifecycle event, so a retried operation can never
charge twice (unique constraint backstop plus status gates).
"""
from datetime import datetime,timezone
from decimal import Decimal
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select,func
from .models import Estimate,MachineSlot,Mould
from .project_models import Project,BudgetHold,BudgetLedger
from .execution_models import TrialExecution,CostReview
from .services import item,serialize
from . import cost_model

def now():return datetime.now(timezone.utc).isoformat()
def cents(v):return Decimal(str(v)).quantize(Decimal('.01'))
def project_view(s,p):
    d=serialize(p)
    d['available']=float(p.budget_total-p.held_amount-p.spent_amount)
    d['active_holds']=s.scalar(select(func.count()).select_from(BudgetHold).where(BudgetHold.project_id==p.id,BudgetHold.state=='active'))
    return d
def hold_view(s,h):
    d=serialize(h)
    d['estimate_code']=item(s,Estimate,h.estimate_id).code
    d['project_code']=item(s,Project,h.project_id).code
    return d
def ledger_view(s,x):
    d=serialize(x)
    d['estimate_code']=item(s,Estimate,x.estimate_id).code if x.estimate_id else ''
    return d
def ledger_entry(s,project,hold,estimate,entry_type,amount,note,request_id):
    # Call after mutating project counters: held_after/spent_after store the post state.
    s.add(BudgetLedger(project_id=project.id,hold_id=hold.id if hold else None,estimate_id=estimate.id if estimate else None,entry_type=entry_type,amount=cents(amount),held_after=project.held_amount,spent_after=project.spent_amount,note=note,request_id=request_id,created_at=now()))
def commit_with_budget(s,eid):
    e=s.query(Estimate).filter_by(id=eid).with_for_update().one_or_none()
    if not e:raise HTTPException(404,'估价不存在')
    if e.status!='requested':raise HTTPException(409,'只有已申请机时的估价可承诺')
    floor=round(e.predicted_hours*e.rate*1.15,2)
    if e.quote_amount<floor:raise HTTPException(409,f'报价低于成本底线 {floor:.2f} 元，请修订报价后重新申请')
    if not e.project_id:raise HTTPException(409,'估单未关联项目预算，不能承诺占款')
    try:model,evidence=cost_model.train(s)
    except ValueError as ex:raise HTTPException(409,str(ex))
    m=item(s,Mould,e.mould_id)
    upper=cents(cost_model.predict_upper(model,m,e.rate,e.predicted_hours))
    project=s.query(Project).filter_by(id=e.project_id).with_for_update().one_or_none()
    if not project:raise HTTPException(404,'项目预算不存在')
    available=project.budget_total-project.held_amount-project.spent_amount
    if upper>available:raise HTTPException(409,f'项目预算余量 ¥{available:.2f} 不足以覆盖成本上分位占款 ¥{upper:.2f}，不能超支承诺')
    slot=s.query(MachineSlot).filter_by(id=e.slot_id).with_for_update().one()
    if e.predicted_hours>slot.capacity_hours-slot.reserved_hours+1e-6:raise HTTPException(409,'机时已被其他承诺占用，不能超售')
    slot.reserved_hours=round(slot.reserved_hours+e.predicted_hours,2)
    project.held_amount+=upper
    basis=dict(evidence);basis['inputs']={'mould_code':m.code,'cavities':m.cavities,'slides':m.slides,'weight_kg':m.weight_kg,'steel':m.steel,'rate':e.rate,'predicted_hours':e.predicted_hours};basis['predicted_upper_cost']=float(upper)
    hold=BudgetHold(estimate_id=e.id,project_id=project.id,amount=upper,hours=e.predicted_hours,basis=basis,state='active',created_at=now())
    s.add(hold);s.flush()
    ledger_entry(s,project,hold,e,'hold',upper,f'承诺占款 {e.code}：机时 {e.predicted_hours}h 与预算 ¥{upper} 同时锁定',f'hold-{e.id}')
    e.status='committed'
    return e,hold
def cancel_estimate(s,eid):
    e=s.query(Estimate).filter_by(id=eid).with_for_update().one_or_none()
    if not e:raise HTTPException(404,'估价不存在')
    if e.status!='committed':raise HTTPException(409,'仅已承诺且未开工的估单可撤单')
    if s.scalar(select(TrialExecution.id).where(TrialExecution.estimate_id==e.id)):raise HTTPException(409,'该估单已开工，不能撤单')
    hold=s.scalar(select(BudgetHold).where(BudgetHold.estimate_id==e.id,BudgetHold.state=='active').with_for_update())
    if hold:
        project=s.query(Project).filter_by(id=hold.project_id).with_for_update().one()
        project.held_amount-=hold.amount
        hold.state='released';hold.settled_at=now()
        ledger_entry(s,project,hold,e,'release',hold.amount,f'未开工撤单，预算占款退回 {e.code}',f'release-{hold.id}')
    if e.slot_id:
        slot=s.query(MachineSlot).filter_by(id=e.slot_id).with_for_update().one()
        slot.reserved_hours=round(max(0.0,slot.reserved_hours-e.predicted_hours),2)
    e.status='cancelled';e.slot_id=None
    return e
def settle_budget(s,review,execution):
    e=item(s,Estimate,execution.estimate_id)
    hold=s.scalar(select(BudgetHold).where(BudgetHold.estimate_id==e.id,BudgetHold.state=='active').with_for_update())
    if not hold:return None
    project=s.query(Project).filter_by(id=hold.project_id).with_for_update().one()
    actual=cents(review.actual_cost)
    if actual>hold.amount:
        topup=actual-hold.amount
        available=project.budget_total-project.held_amount-project.spent_amount
        if topup>available:raise HTTPException(409,f'实际成本超占款 ¥{topup:.2f}，项目预算余量 ¥{available:.2f} 不足，须先补足差额才能签结')
        project.held_amount+=topup;hold.amount+=topup
        ledger_entry(s,project,hold,e,'topup',topup,f'签结补差 {e.code}：实际成本超出原占款',f'topup-{hold.id}')
    project.held_amount-=actual;project.spent_amount+=actual
    ledger_entry(s,project,hold,e,'writeoff',actual,f'按实际成本核销 {e.code}',f'writeoff-{hold.id}')
    remainder=hold.amount-actual
    if remainder>0:
        project.held_amount-=remainder
        ledger_entry(s,project,hold,e,'refund',remainder,f'签结退余款 {e.code}',f'refund-{hold.id}')
    hold.state='settled';hold.settled_at=now()
    return hold
def adjust_budget(s,pid,delta):
    p=s.query(Project).filter_by(id=pid).with_for_update().one_or_none()
    if not p:raise HTTPException(404,'项目不存在')
    change=cents(delta)
    if change==0:raise HTTPException(409,'调整金额不能为零')
    if p.budget_total+change<p.held_amount+p.spent_amount:raise HTTPException(409,'预算总额不能调减到低于已在占与已核销之和')
    p.budget_total+=change
    ledger_entry(s,p,None,None,'adjust',change,f'调整项目预算 {p.code} {"追加" if change>0 else "调减"}',f'adjust-{p.id}-{uuid4().hex[:10]}')
    return p
def pipeline(s,project_id):
    rows=[]
    for e in s.scalars(select(Estimate).where(Estimate.project_id==project_id).order_by(Estimate.id)):
        x=s.scalar(select(TrialExecution).where(TrialExecution.estimate_id==e.id))
        r=s.scalar(select(CostReview).where(CostReview.execution_id==x.id)) if x else None
        hold=s.scalar(select(BudgetHold).where(BudgetHold.estimate_id==e.id))
        if e.status in('draft','calculating','estimated'):stage,blocker='待申请机时','估算或报价未完成，尚未申请机时'
        elif e.status=='requested':stage,blocker='待承诺','负责人承诺时同时占机时与项目预算'
        elif e.status=='cancelled':stage,blocker='已撤单','预算占款与机时已退回，不会核销'
        elif not x:stage,blocker='待开工','机时与预算在占，等待现场开工；未开工可撤单退回'
        elif x.status=='running':stage,blocker='试模进行中','完工后才能建立偏差结算'
        elif not r:stage,blocker='待建结算','实绩已完工，待建立偏差结算复核'
        elif r.status=='draft':
            if hold and hold.state=='active' and cents(r.actual_cost)>hold.amount:stage,blocker='超额待补足',f'实际成本超占款 ¥{cents(r.actual_cost)-hold.amount}，签结须先从项目预算补足差额'
            else:stage,blocker='待签结','结算草稿待负责人签结，签结按实际成本核销并退余款'
        else:stage,blocker='已签结','预算已按实际成本核销，余款已退回项目'
        rows.append({'estimate_id':e.id,'estimate_code':e.code,'estimate_status':e.status,'execution_status':x.status if x else None,'review_status':r.status if r else None,'hold_state':hold.state if hold else None,'hold_amount':float(hold.amount) if hold else None,'stage':stage,'blocker':blocker})
    return rows
