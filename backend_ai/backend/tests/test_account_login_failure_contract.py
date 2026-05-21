from threading import Lock

from app.services.control_plane_service import MT5ControlPlaneService


class _FakeRepo:
    def __init__(self):
        self.runtime_login_result = None

    def ensure_user(self, *, telegram_id, username):
        return {"id": 7, "telegram_id": telegram_id, "username": username}

    def get_account(self, *, account_id, user_id):
        assert account_id == 123
        assert user_id == 7
        return {"id": account_id, "user_id": user_id}

    def mark_account_runtime_login_result(self, *, account_id, ok, error_text=None):
        self.runtime_login_result = {
            "account_id": account_id,
            "ok": ok,
            "error_text": error_text,
        }


class _FakeStore:
    def __init__(self):
        self.audit = None

    def add_audit(self, **kwargs):
        self.audit = kwargs


def test_mark_account_login_request_failed_uses_repo_error_text_contract():
    repo = _FakeRepo()
    store = _FakeStore()
    service = MT5ControlPlaneService.__new__(MT5ControlPlaneService)
    service._repo = repo
    service._store = store
    service._dashboard_cache = {}
    service._dashboard_cache_lock = Lock()

    service.mark_account_login_request_failed(
        telegram_id="5573261363",
        username="admin",
        account_id=123,
        reason="no_scheduler_candidate",
    )

    assert repo.runtime_login_result == {
        "account_id": 123,
        "ok": False,
        "error_text": "no_scheduler_candidate",
    }
    assert store.audit["action"] == "account.login_slot.request_failed"
    assert store.audit["result"] == "login_failed"
