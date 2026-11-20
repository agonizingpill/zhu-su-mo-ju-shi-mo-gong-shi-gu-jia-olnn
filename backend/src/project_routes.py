from fastapi import APIRouter,Depends
from sqlalchemy import select
from .db import db_session
from .auth import current,admin
from .project_models import Project,BudgetHold,BudgetLedger
from .project_schemas import ProjectInput,BudgetAdjust
from .services import record
from . import project_service as svc
router=APIRouter()
@router.get('/projects')
def projects(s=Depends(db_session),u=Depends(current)):return [svc.project_view(s,p) for p in s.scalars(select(Project).order_by(Project.id))]
@router.post('/projects')
def create(p:ProjectInput,s=Depends(db_session),u=Depends(admin)):
    x=Project(**p.model_dump(),held_amount=0,spent_amount=0);s.add(x);s.flush();record(s,u,'建立项目预算 '+x.code);s.commit();return svc.project_view(s,x)
@router.post('/projects/{pid}/budget')
def adjust(pid:int,p:BudgetAdjust,s=Depends(db_session),u=Depends(admin)):
    x=svc.adjust_budget(s,pid,p.delta);record(s,u,'调整项目预算 '+x.code);s.commit();return svc.project_view(s,x)
@router.get('/projects/{pid}/ledger')
def ledger(pid:int,s=Depends(db_session),u=Depends(current)):return [svc.ledger_view(s,x) for x in s.scalars(select(BudgetLedger).where(BudgetLedger.project_id==pid).order_by(BudgetLedger.id.desc()).limit(100))]
@router.get('/projects/{pid}/pipeline')
def pipeline(pid:int,s=Depends(db_session),u=Depends(current)):return svc.pipeline(s,pid)
@router.get('/budget-holds')
def holds(s=Depends(db_session),u=Depends(current)):return [svc.hold_view(s,h) for h in s.scalars(select(BudgetHold).order_by(BudgetHold.id.desc()).limit(200))]
