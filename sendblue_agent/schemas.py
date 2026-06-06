from typing import Optional
from pydantic import BaseModel

class SendblueWebhookPayload(BaseModel):
    number: str
    content: str
    status: Optional[str] = None

class ProactiveAlertRequest(BaseModel):
    target_number: str
    asset: str
    anomaly_type: str
    current_price: float
    reason: str