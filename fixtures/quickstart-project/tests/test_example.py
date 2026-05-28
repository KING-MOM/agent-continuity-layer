"""Quickstart fixture test. Same trivial as src/example.py — the fake
worker (M11.1) won't run this; it's here so the fixture has the shape
of a real project (src + tests)."""

from src.example import greet


def test_greet():
    assert greet("world") == "hello, world"
