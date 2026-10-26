"""Pure schema boundary: specifications may reference only their own document."""


def check_schema_safety(schema):
    count = 0
    active = set()

    def visit(value, depth=0):
        nonlocal count
        count += 1
        if depth > 24 or count > 4000:
            raise ValueError("JSON Schema exceeds the supported size")
        if not isinstance(value, dict | list):
            return
        if id(value) in active:
            raise ValueError("Cyclic JSON Schema object")
        active.add(id(value))
        if isinstance(value, dict):
            if "$id" in value:
                raise ValueError("JSON Schema $id is forbidden")
            for key in ("$ref", "$dynamicRef"):
                if key in value and (not isinstance(value[key], str) or not value[key].startswith("#/")):
                    raise ValueError("Only local JSON Schema references are allowed")
            children = value.values()
        else:
            children = value
        for child in children:
            visit(child, depth + 1)
        active.remove(id(value))

    visit(schema)
