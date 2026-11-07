from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class PageResult:
    page_no: int
    text: str
    method: str                      # text | ocr | spreadsheet | failed
    confidence: Optional[float] = None
    error: Optional[str] = None


@dataclass
class Issue:
    code: str
    severity: str                    # error | warning
    message: str
    field: Optional[str] = None

    def to_dict(self):
        return asdict(self)


@dataclass
class Segment:
    start: int
    end: int
    text: str
    pages: list = field(default_factory=list)


FIELDS = ["invoice_number", "invoice_date", "supplier_name", "customer_name",
          "subtotal", "tax", "total", "currency"]
