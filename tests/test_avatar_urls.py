from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

from app.core.security import CurrentUser
from app.core import config
from app.infrastructure import avatar_urls


def signed(path="avatar/7.png", *, age=0, expires=86400):
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=age)).strftime("%Y%m%dT%H%M%SZ")
    return "https://example.r2.cloudflarestorage.com/avatars/" + path + "?" + urlencode({"X-Amz-Date": stamp, "X-Amz-Expires": expires, "X-Amz-Signature": "a" * 64})


def configure(monkeypatch):
    monkeypatch.setattr(config, "AVATAR_R2_ENDPOINT", "https://example.r2.cloudflarestorage.com")
    monkeypatch.setattr(config, "R2_BUCKET_AVATARS", "avatars")
    monkeypatch.setattr(config, "AVATAR_REDIS_URL", "redis://localhost")
    monkeypatch.setattr(config, "AVATAR_R2_ACCESS_KEY_ID", None)
    monkeypatch.setattr(config, "AVATAR_R2_SECRET_ACCESS_KEY", None)


def test_cache_only_after_authorization_and_valid_object(monkeypatch):
    configure(monkeypatch)
    seen = []
    def authorized(user, emails):
        seen.append(("authorization", user.uid, emails))
        return [("ana@example.com", 7, "avatar/7.png")]
    monkeypatch.setattr(avatar_urls, "_authorized_photos", authorized)
    class Cache:
        def mget(self, keys):
            seen.append(("cache", keys)); return [signed()]
        def close(self): pass
    with patch("redis.Redis.from_url", return_value=Cache()):
        result = avatar_urls.resolve_avatar_urls(CurrentUser(uid="gestor", role="GESTOR"), ["ana@example.com", "other@example.com"])
    assert list(result) == ["ana@example.com"]
    assert seen[0][0] == "authorization" and seen[1] == ("cache", ["usuario:foto:7"])
    assert avatar_urls._valid_url(signed(), "avatar/7.png")
    assert not avatar_urls._valid_url(signed("avatar/other.png"), "avatar/7.png")
    assert not avatar_urls._valid_url(signed(age=90000), "avatar/7.png")
    assert not avatar_urls._valid_url(signed().replace("example.r2", "evil.r2"), "avatar/7.png")


def test_invalid_cache_generates_signed_url_and_sets_ttl(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(config, "AVATAR_R2_ACCESS_KEY_ID", "fake")
    monkeypatch.setattr(config, "AVATAR_R2_SECRET_ACCESS_KEY", "fake")
    monkeypatch.setattr(avatar_urls, "_authorized_photos", lambda *_: [("ana@example.com", 7, "avatar/7.png")])
    cache = SimpleNamespace(mget=lambda _: ["https://evil.example/avatar"], set=lambda *args, **kw: writes.append((args, kw)), close=lambda: None)
    writes = []
    client = SimpleNamespace(generate_presigned_url=lambda *a, **kw: signed())
    with patch("redis.Redis.from_url", return_value=cache), patch("boto3.client", return_value=client):
        urls = avatar_urls.resolve_avatar_urls(CurrentUser(uid="gestor", role="GESTOR"), ["ana@example.com"])
    assert "ana@example.com" in urls and writes[0][0][0] == "usuario:foto:7" and writes[0][1]["ex"] == 86370


def test_authorization_failure_does_not_read_cache(monkeypatch):
    configure(monkeypatch)
    def blocked(*_): raise RuntimeError("sensitive exception")
    monkeypatch.setattr(avatar_urls, "_authorized_photos", blocked)
    with patch("redis.Redis.from_url") as cache:
        assert avatar_urls.resolve_avatar_urls(CurrentUser(uid="user", role="GESTOR"), ["ana@example.com"]) == {}
        cache.assert_not_called()


def test_shared_java_avatar_keys_are_read_in_one_batch_without_renewal(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(avatar_urls,'_authorized_photos',lambda *a:[('a@example.com',7,'avatar/7.png'),('b@example.com',8,'avatar/8.png')])
    calls=[]
    class Cache:
        def mget(self,keys):
            calls.append(keys)
            return [signed('avatar/7.png'),signed('avatar/8.png')]
        def set(self,*a,**kw):raise AssertionError('Hit não pode renovar validade.')
        def close(self):pass
    with patch('redis.Redis.from_url',return_value=Cache()),patch('boto3.client') as signer:
        result=avatar_urls.resolve_avatar_urls(CurrentUser(uid='viewer',role='GESTOR'),['a@example.com','b@example.com'])
    assert calls==[['usuario:foto:7','usuario:foto:8']]
    assert len(result)==2 and not signer.called


def test_authorization_query_limits_manager_to_own_scope(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://unused")
    calls = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query, params): calls.append((query, params))
        def fetchall(self): return []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def cursor(self): return Cursor()
    monkeypatch.setattr(avatar_urls, "read_only_connection", lambda _: Connection())
    for role in ("GESTOR", "GESTOR_WORKSPACE"):
        avatar_urls._authorized_photos(CurrentUser(uid="owner", role=role), ["ana@example.com"])
        query, params = calls[-1]
        assert params == ["owner", role, ["ana@example.com"]]
        assert "target.unidade_id = viewer.unidade_id" in query
        assert "target_unit.workspace_id = viewer_unit.workspace_id" in query
        assert "viewer.tipo = %s" in query and "viewer.status = 'ATIVO'" in query
    count = len(calls)
    assert avatar_urls._authorized_photos(CurrentUser(uid="owner", role="COLABORADOR"), ["ana@example.com"]) == []
    assert len(calls) == count


def test_own_photo_uses_authenticated_uid_before_cache(monkeypatch):
    configure(monkeypatch)
    seen = []
    def own(user):
        seen.append(user.uid)
        return [("__self__", 7, "avatar/7.png")]
    monkeypatch.setattr(avatar_urls, "_authorized_own_photos", own)
    monkeypatch.setattr(avatar_urls, "_authorized_photos", lambda *args: (_ for _ in ()).throw(AssertionError("other user lookup")))
    class Cache:
        def mget(self, keys):
            assert seen == ["owner"]
            assert keys == ["usuario:foto:7"]
            return [signed()]
        def close(self): pass
    with patch("redis.Redis.from_url", return_value=Cache()):
        result = avatar_urls.resolve_avatar_urls(CurrentUser(uid="owner", role="COLABORADOR"), ["__self__"], own=True)
    assert avatar_urls._valid_url(result["__self__"], "avatar/7.png")


def test_own_photo_query_has_no_external_selector(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test")
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query, params):
            assert "usuario.firebase_uid = %s" in query
            assert "usuario.tipo = %s" in query
            assert params == ["owner", "COLABORADOR"]
        def fetchall(self): return []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def cursor(self): return Cursor()
    monkeypatch.setattr(avatar_urls, "read_only_connection", lambda *args: Connection())
    assert avatar_urls._authorized_own_photos(CurrentUser(uid="owner", role="COLABORADOR")) == []


def test_batch_cache_failure_keeps_authorized_photo_fallback(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(config, 'AVATAR_R2_ACCESS_KEY_ID', 'fake')
    monkeypatch.setattr(config, 'AVATAR_R2_SECRET_ACCESS_KEY', 'fake')
    monkeypatch.setattr(avatar_urls, '_authorized_photos', lambda *_: [('ana@example.com', 7, 'avatar/7.png')])
    class Cache:
        def mget(self, keys): raise ConnectionError('simulated')
        def close(self): pass
    client = SimpleNamespace(generate_presigned_url=lambda *a, **kw: signed())
    with patch('redis.Redis.from_url', return_value=Cache()), patch('boto3.client', return_value=client):
        result = avatar_urls.resolve_avatar_urls(CurrentUser(uid='viewer', role='GESTOR'), ['ana@example.com'])
    assert list(result) == ['ana@example.com']
