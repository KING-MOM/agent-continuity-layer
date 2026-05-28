"""Quickstart fixture module. Intentionally trivial — the fake worker
in M11.1 doesn't read or modify these contents; they exist so the
fixture repo looks like an actual project rather than an empty dir."""


def greet(name: str) -> str:
    return f"hello, {name}"
