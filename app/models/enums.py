import enum


class IssueCategoryEnum(str, enum.Enum):
    """Values match the frontend IssueCategory type exactly."""
    maintenance = "Maintenance"
    it_support = "IT Support"
    compliance = "Compliance"
    training = "Training"
    procurement = "Procurement"
    marketing = "Marketing"
    asset_purchase = "Asset Purchase"
    guest_service = "Guest Service"
    other = "Other"


class PriorityEnum(str, enum.Enum):
    """Values match the frontend Priority type exactly."""
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class IssueStatusEnum(str, enum.Enum):
    """Values match the frontend IssueStatus type exactly (note: in-progress uses a hyphen)."""
    open = "open"
    assigned = "assigned"
    in_progress = "in-progress"
    waiting = "waiting"
    resolved = "resolved"
    closed = "closed"


class TaskStatusEnum(str, enum.Enum):
    """Same values as IssueStatusEnum. Frontend uses TaskStatus = IssueStatus."""
    open = "open"
    assigned = "assigned"
    in_progress = "in-progress"
    waiting = "waiting"
    resolved = "resolved"
    closed = "closed"


class ApprovalTypeEnum(str, enum.Enum):
    """Values match the frontend ApprovalType type exactly (asset-purchase uses a hyphen)."""
    procurement = "procurement"
    marketing = "marketing"
    training = "training"
    asset_purchase = "asset-purchase"


class ApprovalStatusEnum(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class OutletStatusEnum(str, enum.Enum):
    operational = "operational"
    warning = "warning"
    critical = "critical"


class CategoryTypeEnum(str, enum.Enum):
    operations = "operations"
    maintenance = "maintenance"


class AssetStatusEnum(str, enum.Enum):
    operational = "operational"
    warning = "warning"
    maintenance = "maintenance"
    critical = "critical"


class WorkOrderTypeEnum(str, enum.Enum):
    corrective = "corrective"
    preventive = "preventive"


class WorkOrderStatusEnum(str, enum.Enum):
    scheduled = "scheduled"
    in_progress = "in-progress"
    completed = "completed"
    cancelled = "cancelled"
