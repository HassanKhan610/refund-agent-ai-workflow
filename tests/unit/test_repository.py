import pytest

from app.data.repository import DataQualityError, NotFoundError


def test_get_existing_order(repo):
    assert repo.get_order("ORD-1001").customer_id == "C-001"


def test_unknown_order_raises_not_found(repo):
    with pytest.raises(NotFoundError):
        repo.get_order("ORD-9999")


def test_quarantined_order_raises_data_quality_error(repo):
    with pytest.raises(DataQualityError, match="failed validation"):
        repo.get_order("ORD-1006")


def test_mark_refunded_updates_status(repo):
    repo.mark_refunded("ORD-1001")
    assert repo.get_order("ORD-1001").status == "refunded"
