"""Scaffold smoke test (RU-1).

Guarantees the package import graph is intact and every layer package is
importable, so the suite is green from the very first commit. Each layer is
filled in by its owner per the build plan.
"""

import importlib

import careline


def test_package_version_exposed():
    assert isinstance(careline.__version__, str)
    assert careline.__version__


def test_every_layer_package_imports():
    layers = [
        "careline.domain",
        "careline.domain.model",
        "careline.domain.brain",
        "careline.domain.gates",
        "careline.domain.rails",
        "careline.domain.scoring",
        "careline.domain.ports",
        "careline.adapters",
        "careline.adapters.orchestration",
        "careline.adapters.llm",
        "careline.adapters.memory",
        "careline.adapters.mongo",
        "careline.services",
        "careline.api",
    ]
    for name in layers:
        assert importlib.import_module(name) is not None
