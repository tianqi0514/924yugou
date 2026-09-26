"""Evaluate reviewed chapter conditions against an immutable Decimal run snapshot."""

from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException

from .rules import EvalValue, RuleError, evaluate, parse_expression, unit_dimension


def condition_results(snapshot: dict, sections: list[dict]) -> dict[str, dict]:
    definitions = {item["key"]: item for item in snapshot["definitions"]}
    results = snapshot["results"]
    outcomes: dict[str, dict] = {}
    for section in sections:
        for condition in section.get("conditions", []):
            expression = condition["expression"]
            _, deps = parse_expression(expression)
            missing = [key for key in deps if results[key]["value"] is None]
            key = f"{section['id']}:{condition['id']}"
            outcome = None
            if not missing:
                try:
                    values = {name: EvalValue(Decimal(results[name]["value"]),
                                              unit_dimension(definitions[name]["unit"]),
                                              definitions[name]["unit"]) for name in deps}
                    evaluated = evaluate(expression, values)
                except RuleError as exc:
                    raise HTTPException(400, f"章节条件 {condition['id']}：{exc}") from exc
                if not isinstance(evaluated.value, bool):
                    raise HTTPException(400, f"章节条件 {condition['id']} 必须返回是/否")
                outcome = evaluated.value
            outcomes[key] = {"section_id": section["id"], "condition_id": condition["id"],
                             "expression": expression, "deps": deps, "missing": missing,
                             "outcome": outcome,
                             "text": None if outcome is None else
                             condition["when_true"] if outcome else condition["when_false"]}
    return outcomes
