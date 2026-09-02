from pydantic import BaseModel, Field
from enum import Enum

class Category(str, Enum):
    BILLING = "billing"
    TECHNICAL = "technical"
    ACCOUNT = "account"
    OTHER = "other"

class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class TicketClassification(BaseModel):
    category: Category
    priority: Priority
    summary: str = Field(..., description="A one-sentence summary.")