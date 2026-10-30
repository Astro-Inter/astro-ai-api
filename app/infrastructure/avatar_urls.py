"""Avatares de RH somente após revalidar o escopo atual no PostgreSQL."""
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs, unquote

from app.core import config
from app.core.security import CurrentUser
from app.infrastructure.database.connections import read_only_connection

logger = logging.getLogger(__name__)


def _valid_url(url: str, path: str) -> bool:
    try:
        parsed, endpoint = urlparse(url), urlparse(config.AVATAR_R2_ENDPOINT or "")
        if parsed.scheme != "https" or not endpoint.hostname or parsed.username or parsed.password:
            return False
        if parsed.hostname not in {endpoint.hostname, f"{config.R2_BUCKET_AVATARS}.{endpoint.hostname}"}:
            return False
        expected_path = "/" + path.lstrip("/")
        if parsed.hostname == endpoint.hostname:
            expected_path = "/" + config.R2_BUCKET_AVATARS + expected_path
        if unquote(parsed.path) != expected_path:
            return False
        params = parse_qs(parsed.query)
        if not re.fullmatch(r"[0-9a-fA-F]{64}", params["X-Amz-Signature"][0]):
            return False
        stamp = datetime.strptime(params["X-Amz-Date"][0], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        expires = int(params["X-Amz-Expires"][0])
        return 0 < expires <= 604800 and stamp.timestamp() + expires > datetime.now(timezone.utc).timestamp() + 30
    except (ValueError, KeyError, IndexError, TypeError):
        return False


def _authorized_photos(user: CurrentUser, emails: list[str]):
    if user.role not in {"GESTOR", "GESTOR_WORKSPACE"} or not config.DATABASE_URL:
        return []
    with read_only_connection(config.DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT target.email, target.id_usuario, photo.caminho_objeto
                  FROM usuario target
                  JOIN unidade target_unit ON target_unit.id_unidade = target.unidade_id
                  JOIN usuario viewer ON viewer.firebase_uid = %s AND viewer.tipo = %s AND viewer.status = 'ATIVO'
                  JOIN unidade viewer_unit ON viewer_unit.id_unidade = viewer.unidade_id
                  JOIN usuario_foto_perfil photo ON photo.usuario_id = target.id_usuario
                 WHERE target.email = ANY(%s) AND target.firebase_uid <> viewer.firebase_uid
                   AND ((viewer.tipo = 'GESTOR' AND target.unidade_id = viewer.unidade_id
                         AND target.tipo IN ('GESTOR', 'COLABORADOR'))
                     OR (viewer.tipo = 'GESTOR_WORKSPACE' AND target_unit.workspace_id = viewer_unit.workspace_id
                         AND target.tipo IN ('GESTOR', 'GESTOR_WORKSPACE', 'COLABORADOR')))
            """, [user.uid, user.role, emails])
            return cur.fetchall()


def resolve_avatar_urls(user: CurrentUser, emails: list[str]) -> dict[str, str]:
    """Sem URLs ou conteúdo pessoal em logs; falha no avatar não quebra o chat."""
    emails = list(dict.fromkeys(email for email in emails if isinstance(email, str) and 0 < len(email) <= 255))[:50]
    endpoint = urlparse(config.AVATAR_R2_ENDPOINT or "")
    if (not emails or not config.R2_BUCKET_AVATARS or endpoint.scheme != "https" or
            not endpoint.hostname or not endpoint.hostname.endswith(".r2.cloudflarestorage.com") or
            endpoint.username or endpoint.password or endpoint.path not in ("", "/") or endpoint.query or endpoint.fragment):
        return {}
    try:
        photos = _authorized_photos(user, emails)
    except Exception:
        logger.warning("Autorização de avatares indisponível")
        return {}
    if not photos:
        return {}
    cache = None
    if config.AVATAR_REDIS_URL:
        try:
            import redis
            cache = redis.Redis.from_url(config.AVATAR_REDIS_URL, decode_responses=True,
                                         socket_timeout=2, socket_connect_timeout=2)
        except Exception:
            logger.warning("Cache de avatares indisponível")
    urls = {}
    client = None
    try:
        for email, user_id, path in photos:
            if email not in emails or not isinstance(user_id, int) or user_id <= 0 or not isinstance(path, str) or not path:
                continue
            key = f"user:avatar:{user_id}"
            url = None
            if cache:
                try: url = cache.get(key)
                except Exception:
                    logger.warning("Cache de avatares indisponível")
                    try: cache.close()
                    except Exception: pass
                    cache = None
            if not url or not _valid_url(url, path):
                if not config.AVATAR_R2_ACCESS_KEY_ID or not config.AVATAR_R2_SECRET_ACCESS_KEY:
                    continue
                try:
                    if client is None:
                        import boto3
                        from botocore.config import Config
                        client = boto3.client("s3", endpoint_url=config.AVATAR_R2_ENDPOINT, region_name="auto",
                            aws_access_key_id=config.AVATAR_R2_ACCESS_KEY_ID, aws_secret_access_key=config.AVATAR_R2_SECRET_ACCESS_KEY,
                            config=Config(signature_version="s3v4", connect_timeout=3, read_timeout=5, retries={"max_attempts": 1}))
                    url = client.generate_presigned_url("get_object", Params={"Bucket": config.R2_BUCKET_AVATARS, "Key": path}, ExpiresIn=86400)
                    if not _valid_url(url, path): continue
                    if cache:
                        try: cache.set(key, url, ex=86370)
                        except Exception:
                            logger.warning("Cache de avatares indisponível")
                            try: cache.close()
                            except Exception: pass
                            cache = None
                except Exception:
                    logger.warning("Geração de avatar indisponível")
                    continue
            urls[email] = url
    finally:
        if cache:
            try: cache.close()
            except Exception: pass
    return urls
