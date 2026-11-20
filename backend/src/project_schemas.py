from pydantic import BaseModel,Field
class ProjectInput(BaseModel):
    code:str=Field(min_length=2,max_length=40)
    name:str=Field(min_length=2,max_length=120)
    budget_total:float=Field(gt=0,le=100000000)
class BudgetAdjust(BaseModel):
    delta:float=Field(gt=-100000000,le=100000000)
