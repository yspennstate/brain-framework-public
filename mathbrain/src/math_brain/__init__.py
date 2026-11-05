"""Privacy-isolated mathematical research memory and verification."""

from .benchmark import (
    BenchmarkItem,
    BenchmarkManifest,
    CounterRNG,
    Provenance,
    SolverView,
    build_manifest,
    certify_item,
    generate_bank,
    generate_item,
    generate_manifest,
    replay_repair,
    validate_problem_binding,
    validate_solution,
    verify_manifest,
)
from .ingestion import (
    BundleArtifactReceipt,
    BundleEndpoint,
    BundleReceipt,
    BundleReleaseManifest,
    ClaimSourceBinding,
    EndpointScope,
    IngestionCoreBinding,
    MathBundleEntry,
    MathKnowledgeBundle,
    MathRelationSpec,
    ReleaseDisposition,
    ReleaseVerifier,
    SourceArtifactBinding,
    SourceExcerptBinding,
    StoreBuildSeal,
    StoredMathBundle,
    TransitiveSourceManifest,
    ingest_bundle,
    release_authorization_mac,
    text_digest,
)
from .lean import (
    FidelityEvidenceAttempt,
    FormalStatementBinding,
    LeanEvidenceAttempt,
    LeanResult,
    LocalFormalizationFidelityAuthority,
    LocalLeanEvidenceAuthority,
    StatementFidelityReview,
    bind_formal_statement,
    check_lean_file,
    claim_statement_hash,
)
from .mistakes import MistakeKind, MistakeRecord, ReflexRecord
from .models import (
    Claim,
    ClaimKind,
    EdgeKind,
    EdgePolarity,
    Evidence,
    EvidenceAttestation,
    EvidenceAuthorityMode,
    EvidenceAuthorityRole,
    EvidenceKind,
    EvidenceReceipt,
    GraphEdge,
    GraphNode,
    NodeKind,
    RecordStatus,
    VerificationEvent,
)
from .overlay import (
    ArtifactReceipt,
    ContentAddressedTextStore,
    MathNodeRevision,
    SourceProvenance,
    StoredMathRevision,
    TrustClass,
    append_revision,
    bridge_edge,
    external_reference_node,
)
from .proofswarm import ProofSwarmImportCandidate, import_proofswarm_ticket
from .retrieval import ExactDependencyClosure, RetrievalMode, exact_dependency_closure
from .store import ClaimBatchItem, ClaimBatchManifest
from .verification import (
    EvidenceKeyPolicy,
    EvidenceVerifier,
    PromotionApplication,
    derive_and_record_status,
    evidence_authorization_mac,
    recompute_current_status,
    register_authenticated_evidence,
)

__all__ = [
    "ArtifactReceipt",
    "BenchmarkItem",
    "BenchmarkManifest",
    "BundleArtifactReceipt",
    "BundleEndpoint",
    "BundleReceipt",
    "BundleReleaseManifest",
    "Claim",
    "ClaimBatchItem",
    "ClaimBatchManifest",
    "ClaimKind",
    "ClaimSourceBinding",
    "ContentAddressedTextStore",
    "CounterRNG",
    "EdgeKind",
    "EdgePolarity",
    "EndpointScope",
    "Evidence",
    "EvidenceAttestation",
    "EvidenceAuthorityMode",
    "EvidenceAuthorityRole",
    "EvidenceKeyPolicy",
    "EvidenceKind",
    "EvidenceReceipt",
    "EvidenceVerifier",
    "ExactDependencyClosure",
    "FidelityEvidenceAttempt",
    "FormalStatementBinding",
    "GraphEdge",
    "GraphNode",
    "IngestionCoreBinding",
    "LeanEvidenceAttempt",
    "LeanResult",
    "LocalFormalizationFidelityAuthority",
    "LocalLeanEvidenceAuthority",
    "MathBundleEntry",
    "MathKnowledgeBundle",
    "MathNodeRevision",
    "MathRelationSpec",
    "MistakeKind",
    "MistakeRecord",
    "NodeKind",
    "PromotionApplication",
    "ProofSwarmImportCandidate",
    "Provenance",
    "RecordStatus",
    "ReflexRecord",
    "ReleaseDisposition",
    "ReleaseVerifier",
    "RetrievalMode",
    "SolverView",
    "SourceArtifactBinding",
    "SourceExcerptBinding",
    "SourceProvenance",
    "StatementFidelityReview",
    "StoreBuildSeal",
    "StoredMathBundle",
    "StoredMathRevision",
    "TransitiveSourceManifest",
    "TrustClass",
    "VerificationEvent",
    "append_revision",
    "bind_formal_statement",
    "bridge_edge",
    "build_manifest",
    "certify_item",
    "check_lean_file",
    "claim_statement_hash",
    "derive_and_record_status",
    "evidence_authorization_mac",
    "exact_dependency_closure",
    "external_reference_node",
    "generate_bank",
    "generate_item",
    "generate_manifest",
    "import_proofswarm_ticket",
    "ingest_bundle",
    "recompute_current_status",
    "register_authenticated_evidence",
    "release_authorization_mac",
    "replay_repair",
    "text_digest",
    "validate_problem_binding",
    "validate_solution",
    "verify_manifest",
]

__version__ = "0.1.0"

# The calibration and recurrence names the research-line tests import from the package root. They
# load on first use, so `import math_brain.retrieval` on the recall hook path does not pay ~15 ms
# for modules it never touches.
_LAZY_EXPORTS = {
    "calibration_plan_v3": (
        "AxisCounts",
        "CalibrationPlan",
        "ManifestRow",
        "MaterializedCaseBinding",
        "SelectionManifest",
        "preregistration_commitment",
        "selection_ticket",
        "validate_materialization",
    ),
    "calibration_protocol_v3": (
        "CalibrationOutcomeBinding",
        "CalibrationOutcomeLink",
        "PreregisteredCorpus",
        "PreregistrationCandidate",
        "VerifierIdentityBinding",
        "bind_calibration_outcomes",
        "build_preregistered_corpus",
        "verify_calibration_outcome_binding",
    ),
    "recurrence_manifest_v3": (
        "RecurrenceBindingBankEntryV3",
        "RecurrenceBindingBankManifestV3",
        "build_preregistered_recurrence_source_role_claim_bank_v3",
        "build_recurrence_binding_bank_manifest_v3",
        "verify_preregistered_recurrence_source_role_claim_bank_v3",
    ),
}
_LAZY_SOURCE = {name: module for module, names in _LAZY_EXPORTS.items() for name in names}


def __getattr__(name: str):
    module = _LAZY_SOURCE.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY_SOURCE))
