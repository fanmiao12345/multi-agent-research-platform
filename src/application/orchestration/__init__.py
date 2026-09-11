# -*- coding: utf-8 -*-
"""S8 自适应多智能体（首版：fixed/fanout 选型）：契约、护栏、调度智能体、执行器。"""
from src.application.orchestration.plan_contract import (  # noqa: F401
    FIRST_VERSION_MODES,
    KNOWN_MODES,
    KNOWN_ROLES,
    Budget,
    ExecutionPlan,
    PlanValidationError,
    SubTask,
    from_plan_dict,
)
from src.application.orchestration.guards import (  # noqa: F401
    FINAL_RESERVE_RATIO,
    SINGLE_CHILD_CAP,
    GuardViolation,
    OrchestrationGuards,
    allocate_budget,
    compute_dispatchable_pool,
    final_reserve_of,
)
from src.application.orchestration.scheduler import (  # noqa: F401
    DEFAULT_BUDGET_CAPS,
    OrchestrationScheduler,
    heuristic_plan,
)
from src.application.orchestration.executor import (  # noqa: F401
    OrchestrationExecutor,
    caps_of,
)
from src.application.orchestration.refs import (  # noqa: F401
    ArtifactRef,
    EvidenceRef,
    SourceRef,
    StructuredSubResult,
    collect_child_refs,
)
from src.application.orchestration.registry import (  # noqa: F401
    MODE_HANDLERS,
    MODE_LABELS,
    handler_name,
)
from src.application.orchestration.contracts import (  # noqa: F401
    DELIVERY_LABELS,
    DELIVERY_LEVELS,
    EXECUTION_STATUSES,
    normalize_level,
    normalize_status,
    unified_record,
)
