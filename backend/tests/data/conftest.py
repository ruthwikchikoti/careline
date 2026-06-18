"""Shared fixtures for the Mongo-backed data tests (NG-6/NG-7).

A fresh in-memory async Mongo (``mongomock-motor``) database per test, so the
repository/memory suite runs offline with no real MongoDB. The same code paths run
against a real server in the testcontainers variant (NG-7) when one is available.

If ``mongomock-motor`` is not installed the whole data-Mongo suite is skipped, so a
minimal install still has a green tree (the offline pure-Python data tests always
run).
"""

import pytest

mongomock_motor = pytest.importorskip("mongomock_motor")


@pytest.fixture()
def mongo_db():
    """A fresh, empty in-memory async Mongo database."""
    client = mongomock_motor.AsyncMongoMockClient()
    return client["careline_test"]
