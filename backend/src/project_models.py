"""Project budget pools, commitment holds and the ledger proving every movement."""
from decimal import Decimal
from sqlalchemy import String,Float,ForeignKey,JSON,CheckConstraint,Numeric
from sqlalchemy.orm import Mapped,mapped_column
from .db import Base
class Project(Base):
    __tablename__='projects'
    __table_args__=(CheckConstraint('budget_total > 0 AND held_amount >= 0 AND spent_amount >= 0 AND held_amount + spent_amount <= budget_total + 0.0001'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(40),unique=True)
    name:Mapped[str]=mapped_column(String(120))
    budget_total:Mapped[Decimal]=mapped_column(Numeric(14,2))
    held_amount:Mapped[Decimal]=mapped_column(Numeric(14,2),default=0)
    spent_amount:Mapped[Decimal]=mapped_column(Numeric(14,2),default=0)
class BudgetHold(Base):
    __tablename__='budget_holds'
    id:Mapped[int]=mapped_column(primary_key=True)
    estimate_id:Mapped[int]=mapped_column(ForeignKey('estimates.id'),unique=True)
    project_id:Mapped[int]=mapped_column(ForeignKey('projects.id'))
    amount:Mapped[Decimal]=mapped_column(Numeric(14,2))
    hours:Mapped[float]=mapped_column(Float)
    basis:Mapped[dict]=mapped_column(JSON)
    state:Mapped[str]=mapped_column(String(20),default='active')
    created_at:Mapped[str]=mapped_column(String(40))
    settled_at:Mapped[str|None]=mapped_column(String(40),nullable=True)
class BudgetLedger(Base):
    __tablename__='budget_ledgers'
    id:Mapped[int]=mapped_column(primary_key=True)
    project_id:Mapped[int]=mapped_column(ForeignKey('projects.id'))
    hold_id:Mapped[int|None]=mapped_column(ForeignKey('budget_holds.id'),nullable=True)
    estimate_id:Mapped[int|None]=mapped_column(ForeignKey('estimates.id'),nullable=True)
    entry_type:Mapped[str]=mapped_column(String(20))
    amount:Mapped[Decimal]=mapped_column(Numeric(14,2))
    held_after:Mapped[Decimal]=mapped_column(Numeric(14,2))
    spent_after:Mapped[Decimal]=mapped_column(Numeric(14,2))
    note:Mapped[str]=mapped_column(String(300),default='')
    request_id:Mapped[str]=mapped_column(String(80),unique=True)
    created_at:Mapped[str]=mapped_column(String(40))
