#!/usr/bin/env python3
"""Validate EduMind backend system requirements (effective/efficient/safe/robust/monitorable/updatable/compounding)."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def has(path):
    return (ROOT / path).exists()


def check_effective():
    p = read("app/agents/pipelines/learning_flow_pipeline.py")
    ok = all(token in p for token in ["Planner", "Validator", "execute_tool("])
    return ok, {
        "pipeline": "app/agents/pipelines/learning_flow_pipeline.py",
        "planner_executor_validator": ok,
    }


def check_efficient():
    budget = read("app/agents/budget.py")
    cache_candidates = [
        "app/services/search/chunker.py",
        "app/services/external_candidate_service.py",
        "app/services/whisper_runtime.py",
    ]
    cache_hit = False
    for c in cache_candidates:
        txt = read(c)
        if any(k in txt for k in ["lru_cache", "CACHE", "cache_hit", "_cache", "_loaded_key"]):
            cache_hit = True
            break

    ok = ("class TokenBudget" in budget and "charge(" in budget and cache_hit)
    return ok, {
        "token_budget": "class TokenBudget" in budget,
        "cache_aware_prompt_or_runtime": cache_hit,
        "budget_file": "app/agents/budget.py",
    }


def check_safe():
    g = read("app/agents/governance/gateway.py")
    ctx = read("app/agents/governance/context.py")
    tools = read("app/agents/governance/tools_learning_flow.py")
    ok = all(
        token in g
        for token in [
            "_TOOL_HANDLERS",
            "_validate_params",
            "tool_not_allowed",
            "GovernanceError",
            "def execute_tool(",
            "governance_execution_context",
        ]
    ) and all(
        token in ctx for token in ["ContextVar", "ensure_in_governance_context", "governance_bypass_blocked"]
    ) and "ensure_in_governance_context()" in tools
    return ok, {
        "governance_gateway": "app/agents/governance/gateway.py",
        "whitelist_and_validation": "_TOOL_HANDLERS" in g and "_validate_params" in g,
        "bypass_block_guard": "ensure_in_governance_context()" in tools,
    }


def check_robust():
    main_txt = read("app/main.py")
    store_txt = read("app/services/search/store.py")
    maint_script = has("scripts/robust_maintenance.py")
    ok = all(
        [
            "recover_interrupted_video_tasks" in main_txt,
            "_get_or_recover_collection" in store_txt,
            maint_script,
        ]
    )
    return ok, {
        "interrupted_task_recovery": "recover_interrupted_video_tasks" in main_txt,
        "collection_recovery": "_get_or_recover_collection" in store_txt,
        "maintenance_script": maint_script,
    }


def check_monitorable():
    pipeline = has("app/analytics/pipeline.py")
    services_pipeline = has("app/services/analytics/pipeline.py")
    services_schema = has("app/services/analytics/schema.py")
    search_logging = read("app/services/search/search_logging.py")
    ok = (
        pipeline
        and services_pipeline
        and services_schema
        and "emit_search_legacy_event" in search_logging
        and "get_telemetry" in search_logging
    )
    return ok, {
        "analytics_pipeline": pipeline,
        "services_analytics_facade": services_pipeline and services_schema,
        "search_logging_adapter": "emit_search_legacy_event" in search_logging,
    }


def check_updatable():
    versions = read("app/agents/prompts/versions.py")
    tools = has("app/agents/governance/tools_learning_flow.py")
    ok = all(
        [
            "LEARNING_FLOW_PROMPT_VERSION" in versions,
            "ORCHESTRATION_PIPELINE_VERSION" in versions,
            tools,
        ]
    )
    return ok, {
        "versioned_prompts": "LEARNING_FLOW_PROMPT_VERSION" in versions,
        "pipeline_versioning": "ORCHESTRATION_PIPELINE_VERSION" in versions,
        "modular_skills_tools": tools,
    }


def check_compounding():
    persist = has("app/services/similarity_audit_log_service.py")
    adapter = has("app/analytics/adapters/similarity.py")
    svc = read("app/services/llm_similarity_service.py")
    ok = persist and adapter and "_record_similarity_audit_log" in svc
    return ok, {
        "trajectory_persistence_service": persist,
        "analytics_adapter": adapter,
        "feedback_record_hook": "_record_similarity_audit_log" in svc,
    }


def main():
    checks = {
        "effective": check_effective(),
        "efficient": check_efficient(),
        "safe": check_safe(),
        "robust": check_robust(),
        "monitorable": check_monitorable(),
        "updatable": check_updatable(),
        "compounding": check_compounding(),
    }

    report = {}
    all_ok = True
    for name, value in checks.items():
        ok, detail = value
        report[name] = {"ok": ok, "detail": detail}
        all_ok = all_ok and ok

    print(json.dumps({"all_ok": all_ok, "report": report}, ensure_ascii=False, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
