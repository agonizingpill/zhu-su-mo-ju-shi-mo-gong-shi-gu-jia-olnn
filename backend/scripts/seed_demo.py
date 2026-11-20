import random
from datetime import datetime,timedelta,timezone
from sqlalchemy import select, func
from src.db import Base,engine,Session
from src.models import User,Mould,MachineSlot,Estimate,TrialHistory
from src.execution_models import TrialExecution,CostReview
from src.workshop_models import MaintenanceWindow,ResinLot,TrialMaterial,ReviewMaterialLedger,TrialDelivery
from src.project_models import Project,BudgetHold,BudgetLedger
from src.auth import hash_password

def seed():
    Base.metadata.create_all(engine)
    with Session() as s:
        for role in ('admin','estimator','viewer'):
            if not s.scalar(select(User).where(User.email==role+'@ruimo.demo')): s.add(User(email=role+'@ruimo.demo',role=role,password_hash=hash_password('Mould2026!')))
        for code,name,cav,slides,steel,kg in [('MD-86','八腔医疗接头模',8,4,'S136',860),('MD-42','双腔仪表外壳模',2,2,'P20',420),('MD-115','十六腔盖件模',16,6,'H13',1150),('MD-07','四腔连接器模',4,2,'P20',310),('MD-233','十二腔齿轮模',12,8,'H13',1480)]:
            if not s.scalar(select(Mould).where(Mould.code==code)): s.add(Mould(code=code,name=name,cavities=cav,slides=slides,steel=steel,weight_kg=kg))
        s.flush()
        for machine,start,cap in [('M280','2026-11-18 08:00',48),('M280','2026-11-19 08:00',4),('M450','2026-11-18 08:00',72)]:
            if not s.scalar(select(MachineSlot).where(MachineSlot.machine==machine,MachineSlot.start_hour==start)): s.add(MachineSlot(machine=machine,start_hour=start,capacity_hours=cap,reserved_hours=0))
        rng=random.Random(42)
        for i in range(160):
            code=f'TR-{i+1:03d}'
            if s.scalar(select(TrialHistory).where(TrialHistory.trial_code==code)):continue
            cav=rng.choice([2,4,8,16]);sl=rng.randint(0,8);kg=rng.uniform(180,1600);steel=rng.choice(['P20','H13','S136'])
            hours=2+cav*.3+sl*.65+kg*.004+cav*sl*.035+(['P20','H13','S136'].index(steel))*.8+rng.gauss(0,.4)
            s.add(TrialHistory(trial_code=code,cavities=cav,slides=sl,steel=steel,weight_kg=round(kg,1),actual_hours=round(hours,2)))
        s.flush()
        for code,name,budget in [('PRJ-HY','华远医疗年度试模包',60000),('PRJ-ST','内部工艺验证预算',25000)]:
            if not s.scalar(select(Project).where(Project.code==code)): s.add(Project(code=code,name=name,budget_total=budget,held_amount=0,spent_amount=0))
        s.flush()
        if not s.scalar(select(Estimate).where(Estimate.code=='QE-26017')):
            m=s.scalar(select(Mould).where(Mould.code=='MD-86'))
            s.add(Estimate(code='QE-26017',mould_id=m.id,rate=280,setup_hours=2,quote_amount=8000,status='draft'))
        s.flush()
        qe=s.scalar(select(Estimate).where(Estimate.code=='QE-26017'))
        hy=s.scalar(select(Project).where(Project.code=='PRJ-HY'))
        if qe and qe.project_id is None and hy: qe.project_id=hy.id
        if not s.scalar(select(Estimate).where(Estimate.code=='QE-FLOOR-DEMO')):
            m=s.scalar(select(Mould).where(Mould.code=='MD-42'));slot=s.scalar(select(MachineSlot).where(MachineSlot.machine=='M450'))
            s.add(Estimate(code='QE-FLOOR-DEMO',mould_id=m.id,slot_id=slot.id,rate=300,setup_hours=1,predicted_hours=10,quote_amount=5000,status='committed',model_result={'note':'已承诺的演示订单，供实绩采集；不作模型效果指标'}));slot.reserved_hours+=10
        # Signed historical settlements train the upper-quantile cost model; five moulds enable group validation.
        hist=random.Random(7)
        moulds={m.code:m for m in s.scalars(select(Mould))}
        group_codes=['MD-86','MD-42','MD-115','MD-07','MD-233']
        for i in range(36):
            code=f'HS-{i+1:03d}'
            if s.scalar(select(Estimate).where(Estimate.code==code)):continue
            m=moulds[group_codes[i%5]]
            rate=hist.choice([240,260,280,300,320])
            base=2+m.cavities*.3+m.slides*.65+m.weight_kg*.004+m.cavities*m.slides*.035+(['P20','H13','S136'].index(m.steel))*.8
            pred=round(base+hist.gauss(0,.3)+1,2)
            actual=round(max(1.0,pred+hist.gauss(0,pred*.12)),2)
            material=round(hist.uniform(150,900),2)
            quote=round((actual*rate+material)*hist.uniform(1.15,1.5),2)
            e=Estimate(code=code,mould_id=m.id,rate=rate,setup_hours=1,predicted_hours=pred,quote_amount=quote,status='committed',model_result={'note':'历史签结基准单，仅供成本模型训练'})
            s.add(e);s.flush()
            start=datetime(2026,9,1,tzinfo=timezone.utc)+timedelta(days=i*3)
            x=TrialExecution(estimate_id=e.id,started_at=start.isoformat(),ended_at=(start+timedelta(hours=actual)).isoformat(),actual_hours=actual,good_shots=96,total_shots=100,snapshot={'estimate_code':code,'predicted_hours':pred,'quoted_amount':quote,'rate':rate,'mould_code':m.code,'slot_id':None},status='settled')
            s.add(x);s.flush()
            cost=round(actual*rate+material,2)
            s.add(CostReview(execution_id=x.id,material_cost=material,actual_cost=cost,quoted_amount=quote,variance_hours=round(actual-pred,4),variance_pct=round((actual-pred)/pred*100,3),margin=round(quote-cost,2),reason='历史基准签结样本',status='signed',signed_by='admin@ruimo.demo'))
        for code,grade,qty,price,state in [('RS-P20-DEMO','ABS自然色',500,20,'available'),('RS-ISOLATED','ABS复检隔离',50,18,'quarantine')]:
            if not s.scalar(select(ResinLot).where(ResinLot.code==code)):
                s.add(ResinLot(code=code,grade=grade,received_kg=qty,available_kg=qty,unit_price=price,state=state))
        s.commit()
        print({t.__tablename__:s.scalar(select(func.count()).select_from(t)) for t in (User,Mould,MachineSlot,Estimate,TrialHistory,TrialExecution,CostReview,MaintenanceWindow,ResinLot,TrialMaterial,ReviewMaterialLedger,TrialDelivery,Project,BudgetHold,BudgetLedger)})
if __name__=='__main__':seed()
