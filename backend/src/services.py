from decimal import Decimal
from datetime import datetime, timezone
from fastapi import HTTPException
from .models import Estimate, Mould, MachineSlot, Audit
from .constants import ESTIMATE_STATES

def record(s,u,action): s.add(Audit(actor=u.email,action=action,stamp=datetime.now(timezone.utc).isoformat()))
def item(s,model,id):
    v=s.get(model,id)
    if v is None: raise HTTPException(404,'记录不存在')
    return v
def serialize(v): return {c.name:float(getattr(v,c.name)) if isinstance(getattr(v,c.name),Decimal) else getattr(v,c.name) for c in v.__table__.columns}
def require_edit(e):
    if e.status in ('committed','calculating'): raise HTTPException(409,'已承诺或计算中的估价不能修改')
def request_capacity(s,e,slot_id):
    if e.status!='estimated': raise HTTPException(409,'请先完成工时估算')
    slot=item(s,MachineSlot,slot_id)
    if e.predicted_hours>slot.capacity_hours-slot.reserved_hours+1e-6: raise HTTPException(409,'机时余量不足，请选择其他时段')
    e.slot_id=slot.id; e.status='requested'
