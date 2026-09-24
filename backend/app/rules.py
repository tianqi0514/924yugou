"""受限 Decimal 规则解析、量纲校验和前向计算。"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from decimal import Decimal, DivisionByZero, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, getcontext
from typing import Any

getcontext().prec = 50


class RuleError(ValueError):
    """不安全、无效或不可计算的规则。"""


@dataclass(frozen=True)
class EvalValue:
    """数值和量纲一起传播，避免只比字符串单位。"""

    value: Decimal | bool
    dimension: tuple[tuple[str, int], ...] = ()
    unit_tag: str = ""


UNIT_ALIASES = {
    "": "",
    "无": "",
    "1": "",
    "%": "",
    "比例": "",
    "元": "currency",
    "万元": "currency",
    "人民币": "currency",
    "套": "count",
    "条": "line",
    "人": "person",
    "小时": "hour",
    "天": "day",
    "年": "year",
    "月": "month",
    "kW": "power",
    "kWh": "energy",
    "平方米": "area",
}


def unit_dimension(unit: str) -> tuple[tuple[str, int], ...]:
    """将常见简单及复合单位映射为可比较的量纲。

    Args:
        unit: 项目事实定义中的显示单位。

    Returns:
        已排序的量纲指数元组。
    """
    if not unit or unit in ("无", "1", "%", "比例"):
        return ()
    parts = unit.replace("／", "/").split("/")
    dimensions: dict[str, int] = {}
    for index, part in enumerate(parts):
        for token in part.split("·"):
            token = token.strip()
            if not token:
                continue
            canonical = UNIT_ALIASES.get(token, token)
            if canonical:
                dimensions[canonical] = dimensions.get(canonical, 0) + (1 if index == 0 else -1)
    return tuple(sorted((key, power) for key, power in dimensions.items() if power))


def _combine(left: tuple, right: tuple, sign: int) -> tuple:
    result = dict(left)
    for key, power in right:
        result[key] = result.get(key, 0) + sign * power
    return tuple(sorted((key, power) for key, power in result.items() if power))


def _compatible(left: EvalValue, right: EvalValue) -> tuple:
    if isinstance(left.value, bool) != isinstance(right.value, bool):
        raise RuleError("布尔值与数值不能直接比较")
    if left.dimension == right.dimension:
        if left.unit_tag and right.unit_tag and left.unit_tag != right.unit_tag:
            raise RuleError("单位不一致；请在规则中显式换算")
        return left.dimension
    # 加减零是业务规则中的常见缺口守卫，例如 max(金额差, 0)。
    if left.value == 0 and not left.dimension:
        return right.dimension
    if right.value == 0 and not right.dimension:
        return left.dimension
    raise RuleError("单位量纲不匹配")


ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)
ALLOWED_COMPARISONS = (ast.Gt, ast.GtE, ast.Lt, ast.LtE, ast.Eq, ast.NotEq)
ALLOWED_FUNCTIONS = {"min", "max", "floor", "ceil"}


def parse_expression(expression: str) -> tuple[ast.Expression, list[str]]:
    """解析白名单表达式并提取事实依赖。

    Args:
        expression: 规则表达式。

    Returns:
        受限 AST 和按出现顺序排列的依赖 key。

    Raises:
        RuleError: 存在任意调用、属性、下标或不支持的语法。
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise RuleError("规则表达式语法错误") from exc
    deps: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Expression | ast.Load):
            continue
        if isinstance(node, ast.BinOp) and isinstance(node.op, ALLOWED_BINOPS):
            continue
        if isinstance(node, ast.Compare) and all(isinstance(op, ALLOWED_COMPARISONS) for op in node.ops):
            continue
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            continue
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ALLOWED_FUNCTIONS and not node.keywords:
            continue
        if isinstance(node, ast.Name):
            if node.id not in ALLOWED_FUNCTIONS and node.id not in deps:
                deps.append(node.id)
            continue
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            continue
        if isinstance(node, ALLOWED_BINOPS + ALLOWED_COMPARISONS + (ast.UAdd, ast.USub)):
            continue
        raise RuleError("规则包含不允许的语法")
    if not deps:
        raise RuleError("规则至少需要引用一个项目事实")
    return tree, deps


def _decimal_constant(node: ast.Constant, expression: str) -> Decimal:
    source = ast.get_source_segment(expression, node)
    try:
        return Decimal(source if source is not None else str(node.value))
    except InvalidOperation as exc:
        raise RuleError("数值常量无效") from exc


def evaluate(expression: str, values: dict[str, EvalValue]) -> EvalValue:
    """在事实环境中计算受限表达式。

    Args:
        expression: 已经过白名单验证的表达式。
        values: 依赖事实的数值与量纲。

    Returns:
        结果及其量纲。

    Raises:
        RuleError: 缺值、除零、单位或结果类型不合法。
    """
    tree, _ = parse_expression(expression)

    def walk(node: ast.AST) -> EvalValue:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise RuleError(f"缺少事实：{node.id}")
            return values[node.id]
        if isinstance(node, ast.Constant):
            return EvalValue(_decimal_constant(node, expression))
        if isinstance(node, ast.UnaryOp):
            item = walk(node.operand)
            if isinstance(item.value, bool):
                raise RuleError("布尔值不能参与算术")
            return EvalValue(-item.value if isinstance(node.op, ast.USub) else item.value, item.dimension, item.unit_tag)
        if isinstance(node, ast.BinOp):
            left, right = walk(node.left), walk(node.right)
            if isinstance(left.value, bool) or isinstance(right.value, bool):
                raise RuleError("布尔值不能参与算术")
            if isinstance(node.op, (ast.Add, ast.Sub)):
                dim = _compatible(left, right)
                result = left.value + right.value if isinstance(node.op, ast.Add) else left.value - right.value
                return EvalValue(result, dim, left.unit_tag or right.unit_tag)
            if isinstance(node.op, ast.Mult):
                return EvalValue(left.value * right.value, _combine(left.dimension, right.dimension, 1))
            if right.value == 0:
                raise RuleError("规则除零")
            try:
                return EvalValue(left.value / right.value, _combine(left.dimension, right.dimension, -1))
            except (DivisionByZero, InvalidOperation) as exc:
                raise RuleError("规则除法无效") from exc
        if isinstance(node, ast.Compare):
            values_for_comparison = [walk(node.left), *[walk(x) for x in node.comparators]]
            outcome = True
            for left, op, right in zip(values_for_comparison, node.ops, values_for_comparison[1:]):
                _compatible(left, right)
                a, b = left.value, right.value
                if isinstance(op, ast.Gt): matched = a > b
                elif isinstance(op, ast.GtE): matched = a >= b
                elif isinstance(op, ast.Lt): matched = a < b
                elif isinstance(op, ast.LtE): matched = a <= b
                elif isinstance(op, ast.Eq): matched = a == b
                else: matched = a != b
                outcome = outcome and matched
            return EvalValue(outcome)
        if isinstance(node, ast.Call):
            args = [walk(x) for x in node.args]
            if not args:
                raise RuleError("函数缺少参数")
            if node.func.id in ("floor", "ceil"):
                if len(args) != 1 or isinstance(args[0].value, bool):
                    raise RuleError("取整函数只接受一个数值")
                rounding = ROUND_FLOOR if node.func.id == "floor" else ROUND_CEILING
                return EvalValue(args[0].value.to_integral_value(rounding=rounding), args[0].dimension, args[0].unit_tag)
            dim = args[0].dimension
            for item in args[1:]:
                dim = _compatible(EvalValue(args[0].value, dim), item)
            if any(isinstance(item.value, bool) for item in args):
                raise RuleError("min/max 只接受数值")
            result = min(x.value for x in args) if node.func.id == "min" else max(x.value for x in args)
            return EvalValue(result, dim, next((item.unit_tag for item in args if item.unit_tag), ""))
        raise RuleError("规则包含不允许的语法")

    return walk(tree)


def sort_rules(rules: list[Any], fact_keys: set[str]) -> list[Any]:
    """校验依赖并按拓扑顺序排列项目规则。

    Args:
        rules: 含 target_key 和 deps 的规则对象。
        fact_keys: 当前项目拥有的事实 key。

    Returns:
        可安全前向执行的规则顺序。

    Raises:
        RuleError: 事实不存在、多个规则写同一目标或存在循环。
    """
    by_target = {}
    for rule in rules:
        if rule.target_key not in fact_keys or any(dep not in fact_keys for dep in rule.deps):
            raise RuleError("规则引用了不存在的项目事实")
        if rule.target_key in by_target:
            raise RuleError("同一事实不能由多个规则写入")
        by_target[rule.target_key] = rule
    visited: set[str] = set()
    visiting: set[str] = set()
    ordered = []

    def visit(target: str) -> None:
        if target in visiting:
            raise RuleError("规则存在循环依赖")
        if target in visited:
            return
        visiting.add(target)
        rule = by_target[target]
        for dep in rule.deps:
            if dep in by_target:
                visit(dep)
        visiting.remove(target)
        visited.add(target)
        ordered.append(rule)

    for key in by_target:
        visit(key)
    return ordered
