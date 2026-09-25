"""Google BotGuard VM 适配器（目标特定逻辑只允许存在于本包）。"""
from .program import BotGuardProgram
from .proof import ProofToken
from .trace_link import EnvInputs, VmExecution
from .chain import build_vm_evidence
from .divergence import DivergenceReport, build_divergence_report

__all__ = ["BotGuardProgram", "ProofToken", "EnvInputs", "VmExecution",
           "build_vm_evidence", "DivergenceReport", "build_divergence_report"]
