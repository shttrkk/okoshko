"""A deliberately small expression interpreter: no eval, calls, imports or I/O."""
import ast


class MissingInput(ValueError):
    pass


def validate_expression(expression: str):
    tree = ast.parse(expression, mode="eval")
    nodes = list(ast.walk(tree))
    allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.Compare,
               ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq, ast.Name, ast.Load, ast.Attribute, ast.Constant)
    if len(nodes)>80 or any(not isinstance(node, allowed) for node in nodes):
        raise ValueError("Only field comparisons joined by and/or/not are supported")
    for node in nodes:
        if isinstance(node, ast.Name) and node.id.startswith("_") or isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError("Private names are forbidden")
    return tree


def evaluate_expression(expression: str, values: dict):
    tree = validate_expression(expression)
    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant):
            if node.value is None:
                raise MissingInput("Null input")
            return node.value
        if isinstance(node, ast.Name):
            value = values.get(node.id)
            if value is None:
                raise MissingInput(node.id)
            return value
        if isinstance(node, ast.Attribute):
            obj = visit(node.value)
            value = obj.get(node.attr) if isinstance(obj, dict) else None
            if value is None:
                raise MissingInput(node.attr)
            return value
        if isinstance(node, ast.BoolOp):
            # Do not short-circuit unknown operands into false certainty.
            operands = [bool(visit(value)) for value in node.values]
            return all(operands) if isinstance(node.op, ast.And) else any(operands)
        if isinstance(node, ast.UnaryOp):
            return not visit(node.operand)
        if isinstance(node, ast.Compare):
            left = visit(node.left)
            for op, other in zip(node.ops, node.comparators, strict=True):
                right = visit(other)
                matched = left<right if isinstance(op, ast.Lt) else left<=right if isinstance(op, ast.LtE) else left>right if isinstance(op, ast.Gt) else left>=right if isinstance(op, ast.GtE) else left==right if isinstance(op, ast.Eq) else left!=right
                if not matched:
                    return False
                left = right
            return True
        raise ValueError("Unsupported expression")
    return bool(visit(tree))

