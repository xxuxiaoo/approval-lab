import pytest

from approval_lab.schemas import ProviderInput
from approval_lab.storage import InstanceLock, Store


def test_only_one_instance_can_own_paid_request_scheduler(tmp_path):
    first, second = InstanceLock(tmp_path), InstanceLock(tmp_path)
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="重复启动"):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


def test_missing_master_key_never_silently_replaces_database_key(tmp_path):
    store = Store(tmp_path)
    store.save_provider(ProviderInput(name="test", base_url="https://relay.example/v1", api_key="private"))
    key = tmp_path / "master.key"
    # Rename inside this test-owned tmp directory; no user data is involved.
    key.rename(tmp_path / "backup.key")
    with pytest.raises(RuntimeError, match="master.key"):
        Store(tmp_path)
    assert not key.exists()
