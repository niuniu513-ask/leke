"""Ownership boundary for native browser attachment."""
from gui_app.browser_workspace import descendants


def test_only_session_descendants_are_owned():
    parents = {10: 1, 11: 10, 12: 11, 20: 1, 21: 20, 30: 999}
    assert descendants(10, parents) == {10, 11, 12}
    assert descendants(0, parents) == set()


def test_cyclic_snapshot_terminates():
    assert descendants(10, {10: 11, 11: 10}) == {10, 11}
