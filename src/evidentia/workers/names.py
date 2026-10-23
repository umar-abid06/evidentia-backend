"""Task names shared by producers (API) and consumers (workers)."""

from __future__ import annotations

PROCESS_DOCUMENT = "evidentia.documents.process"
PUBLISH_POLICY_VERSION = "evidentia.knowledge.publish_version"
TRANSLATE_COVERAGE = "evidentia.knowledge.translate_coverage"
GENERATE_HEALTH_GUIDE = "evidentia.travel.generate_health_guide"
RECOVER_HEALTH_GUIDES = "evidentia.travel.recover_health_guides"
GENERATE_JETLAG_PLAN = "evidentia.travel.generate_jetlag_plan"
SYNC_CRM_CONTACT = "evidentia.integrations.sync_crm_contact"

QUEUES: dict[str, str] = {
    "evidentia.documents.*": "documents",
    "evidentia.knowledge.*": "knowledge",
    "evidentia.travel.*": "generation",
    "evidentia.integrations.*": "integrations",
}
