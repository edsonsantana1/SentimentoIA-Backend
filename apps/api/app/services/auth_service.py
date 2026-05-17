import base64
import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Optional, Tuple

import bcrypt
import pyotp
import qrcode
from bson import ObjectId
from bson.errors import InvalidId

from app.config import settings
from app.database import get_db
from app.schemas import UserCreate, UserRole

logger = logging.getLogger(__name__)

class AuthService:
    """Serviço de autenticação e segurança"""

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(tz=timezone.utc)
    
    @staticmethod
    def hash_password(password: str) -> str:
        """Criptografa uma senha usando bcrypt"""
        salt = bcrypt.gensalt(rounds=12)
        return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")
    
    @staticmethod
    def verify_password(password: str, hashed_password: str) -> bool:
        """Verifica se a senha corresponde ao hash"""
        return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))

    @staticmethod
    def normalize_username(value: str | None) -> str:
        normalized = (value or "").strip().lower()
        normalized = re.sub(r"\s+", ".", normalized)
        normalized = re.sub(r"[^a-z0-9._-]", "", normalized)
        normalized = re.sub(r"[._-]{2,}", ".", normalized).strip("._-")
        return normalized

    @staticmethod
    def _username_exists(username: str, *, exclude_user_id: ObjectId | None = None) -> bool:
        db = get_db()
        query: dict = {"username": username}
        if exclude_user_id is not None:
            query["_id"] = {"$ne": exclude_user_id}
        return db.users.find_one(query) is not None

    @staticmethod
    def _generate_available_username(*, preferred: str | None, fallback_email: str) -> str:
        preferred_username = AuthService.normalize_username(preferred)
        fallback_base = AuthService.normalize_username(fallback_email.split("@", 1)[0])
        base = preferred_username or fallback_base or "usuario"

        if len(base) < 3:
            base = f"usuario{secrets.randbelow(9000) + 1000}"

        candidate = base[:24]
        suffix = 0

        while AuthService._username_exists(candidate):
            suffix += 1
            candidate = f"{base[:20]}{suffix}"

        return candidate
    
    @staticmethod
    def create_user(user_data: UserCreate) -> dict:
        """Cria um novo usuário no banco de dados"""
        db = get_db()
        
        # Verificar se o email já existe
        normalized_email = user_data.email.strip().lower()
        existing_user = db.users.find_one({"email": normalized_email})
        if existing_user:
            raise ValueError("Email já cadastrado")
        
        # Hash da senha
        hashed_password = AuthService.hash_password(user_data.password)
        username = AuthService._generate_available_username(
            preferred=getattr(user_data, "username", None),
            fallback_email=normalized_email,
        )
        
        # Criar documento do usuário
        user_doc = {
            "email": normalized_email,
            "name": user_data.name,
            "username": username,
            "phone": user_data.phone,
            "password_hash": hashed_password,
            "role": UserRole.USER.value,
            "mfa_enabled": False,
            "mfa_verified": False,
            "mfa_secret": None,
            "created_at": AuthService.utcnow(),
            "updated_at": AuthService.utcnow(),
            "last_signed_in": AuthService.utcnow(),
            "is_active": True,
            "password_reset_token_hash": None,
            "password_reset_expires_at": None,
            "password_reset_requested_at": None,
        }
        
        result = db.users.insert_one(user_doc)
        user_doc["_id"] = result.inserted_id
        
        logger.info("✓ Usuário criado: %s", user_data.email)
        return user_doc

    @staticmethod
    def update_profile(user_id: str, *, name: str | None = None, username: str | None = None) -> dict:
        """Atualiza nome e username do usuário autenticado."""
        db = get_db()

        try:
            object_id = ObjectId(user_id)
        except (InvalidId, TypeError) as exc:
            raise ValueError("Usuário inválido") from exc

        user = db.users.find_one({"_id": object_id, "is_active": True})
        if not user:
            raise ValueError("Usuário não encontrado")

        updates: dict[str, str | datetime] = {}

        if name is not None:
            clean_name = " ".join(name.split())
            if len(clean_name) < 2:
                raise ValueError("Nome inválido")
            updates["name"] = clean_name

        if username is not None:
            normalized_username = AuthService.normalize_username(username)
            if len(normalized_username) < 3:
                raise ValueError("Nome de usuário inválido")
            if AuthService._username_exists(normalized_username, exclude_user_id=object_id):
                raise ValueError("Nome de usuário já está em uso")
            updates["username"] = normalized_username

        if not updates:
            return user

        updates["updated_at"] = AuthService.utcnow()
        db.users.update_one({"_id": object_id}, {"$set": updates})

        updated = db.users.find_one({"_id": object_id})
        if not updated:
            raise ValueError("Usuário não encontrado")

        return updated

    @staticmethod
    def change_password(user_id: str, *, current_password: str, new_password: str) -> bool:
        """Troca a senha confirmando a senha atual."""
        db = get_db()

        try:
            object_id = ObjectId(user_id)
        except (InvalidId, TypeError):
            return False

        user = db.users.find_one({"_id": object_id, "is_active": True})
        if not user:
            return False

        if not AuthService.verify_password(current_password, user.get("password_hash", "")):
            return False

        if len(new_password or "") < 8:
            raise ValueError("A nova senha deve ter no mínimo 8 caracteres")

        if AuthService.verify_password(new_password, user.get("password_hash", "")):
            raise ValueError("A nova senha deve ser diferente da atual")

        db.users.update_one(
            {"_id": object_id},
            {
                "$set": {
                    "password_hash": AuthService.hash_password(new_password),
                    "updated_at": AuthService.utcnow(),
                },
                "$unset": {
                    "password_reset_token_hash": "",
                    "password_reset_expires_at": "",
                    "password_reset_requested_at": "",
                },
            },
        )
        return True
    
    @staticmethod
    def get_user_by_email(email: str) -> Optional[dict]:
        """Obtém um usuário pelo email"""
        db = get_db()
        return db.users.find_one({"email": email.strip().lower()})
    
    @staticmethod
    def get_user_by_id(user_id: str) -> Optional[dict]:
        """Obtém um usuário pelo ID"""
        db = get_db()
        try:
            return db.users.find_one({"_id": ObjectId(user_id)})
        except (InvalidId, TypeError):
            return None
    
    @staticmethod
    def authenticate_user(email: str, password: str) -> Optional[dict]:
        """Autentica um usuário com email e senha"""
        normalized_email = email.strip().lower()
        user = AuthService.get_user_by_email(normalized_email)
        
        if not user:
            logger.warning("✗ Tentativa de login com email inexistente: %s", normalized_email)
            return None
        
        if not user.get("is_active"):
            logger.warning("✗ Tentativa de login com usuário inativo: %s", normalized_email)
            return None
        
        if not AuthService.verify_password(password, user.get("password_hash", "")):
            logger.warning("✗ Tentativa de login com senha incorreta: %s", normalized_email)
            return None
        
        # Atualizar último acesso
        db = get_db()
        db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"last_signed_in": AuthService.utcnow(), "updated_at": AuthService.utcnow()}}
        )
        
        logger.info("✓ Usuário autenticado: %s", normalized_email)
        return user
    
    @staticmethod
    def setup_mfa(user_id: str) -> Tuple[str, str]:
        """Configura MFA para um usuário e retorna secret e QR code"""
        db = get_db()

        try:
            target_user_id = ObjectId(user_id)
        except (InvalidId, TypeError) as exc:
            raise ValueError("Usuário inválido") from exc
        
        # Gerar secret TOTP
        secret = pyotp.random_base32()
        
        # Gerar QR code
        totp = pyotp.TOTP(secret)
        qr_uri = totp.provisioning_uri(
            name=f"SentimentoIA ({user_id})",
            issuer_name="SentimentoIA"
        )
        
        # Criar imagem QR code
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(qr_uri)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        qr_code_base64 = base64.b64encode(buffer.getvalue()).decode()
        
        # Salvar secret no banco (ainda não verificado)
        db.users.update_one(
            {"_id": target_user_id},
            {
                "$set": {
                    "mfa_secret": secret,
                    "mfa_enabled": False,
                    "mfa_verified": False,
                    "updated_at": AuthService.utcnow(),
                }
            }
        )
        
        logger.info("✓ MFA configurado para usuário: %s", user_id)
        return secret, qr_code_base64
    
    @staticmethod
    def validate_mfa_code(user_id: str, code: str) -> bool:
        """Valida um código MFA sem alterar o estado do usuário."""
        db = get_db()
        try:
            user = db.users.find_one({"_id": ObjectId(user_id)})
        except (InvalidId, TypeError):
            return False
        
        if not user or not user.get("mfa_secret"):
            return False
        
        totp = pyotp.TOTP(user["mfa_secret"])
        return bool(totp.verify(code, valid_window=1))

    @staticmethod
    def enable_mfa(user_id: str, code: str) -> bool:
        """Habilita MFA após validação do código atual do autenticador."""
        if not AuthService.validate_mfa_code(user_id, code):
            logger.warning("✗ Código MFA inválido para usuário: %s", user_id)
            return False

        db = get_db()
        db.users.update_one(
            {"_id": ObjectId(user_id)},
            {
                "$set": {
                    "mfa_enabled": True,
                    "mfa_verified": True,
                    "updated_at": AuthService.utcnow(),
                }
            },
        )

        logger.info("✓ MFA habilitado para usuário: %s", user_id)
        return True

    @staticmethod
    def verify_mfa_code(user_id: str, code: str) -> bool:
        """Compatibilidade legado: verificar+habilitar MFA."""
        return AuthService.enable_mfa(user_id, code)

    @staticmethod
    def disable_mfa(user_id: str, password: str) -> bool:
        """Desabilita MFA para um usuário, confirmando senha."""
        db = get_db()
        try:
            object_id = ObjectId(user_id)
        except (InvalidId, TypeError):
            return False

        user = db.users.find_one({"_id": object_id})
        if not user:
            return False

        if not AuthService.verify_password(password, user.get("password_hash", "")):
            logger.warning("✗ Senha inválida ao tentar desabilitar MFA: %s", user_id)
            return False

        db.users.update_one(
            {"_id": object_id},
            {
                "$set": {
                    "mfa_enabled": False,
                    "mfa_verified": False,
                    "mfa_secret": None,
                    "updated_at": AuthService.utcnow(),
                }
            },
        )
        logger.info("✓ MFA desabilitado para usuário: %s", user_id)
        return True
        
    @staticmethod
    def create_password_reset_token(email: str) -> tuple[Optional[str], Optional[dict]]:
        """Gera token de reset para usuário ativo. Não expõe erro para e-mail inexistente."""
        user = AuthService.get_user_by_email(email)
        if not user or not user.get("is_active"):
            return None, None

        raw_token = secrets.token_urlsafe(48)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        expires_at = AuthService.utcnow() + timedelta(minutes=max(5, int(settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES)))

        db = get_db()
        db.users.update_one(
            {"_id": user["_id"]},
            {
                "$set": {
                    "password_reset_token_hash": token_hash,
                    "password_reset_expires_at": expires_at,
                    "password_reset_requested_at": AuthService.utcnow(),
                    "updated_at": AuthService.utcnow(),
                }
            },
        )

        logger.info("✓ Token de reset gerado para usuário: %s", email)
        return raw_token, user

    @staticmethod
    def reset_password_with_token(token: str, new_password: str) -> bool:
        """Redefine senha quando token de reset é válido e não expirado."""
        token = (token or "").strip()
        if not token:
            return False

        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = AuthService.utcnow()
        db = get_db()
        user = db.users.find_one(
            {
                "password_reset_token_hash": token_hash,
                "password_reset_expires_at": {"$gt": now},
                "is_active": True,
            }
        )

        if not user:
            logger.warning("✗ Tentativa de reset com token inválido ou expirado")
            return False

        db.users.update_one(
            {"_id": user["_id"]},
            {
                "$set": {
                    "password_hash": AuthService.hash_password(new_password),
                    "updated_at": now,
                },
                "$unset": {
                    "password_reset_token_hash": "",
                    "password_reset_expires_at": "",
                    "password_reset_requested_at": "",
                },
            },
        )

        logger.info("✓ Senha redefinida com sucesso para usuário: %s", user.get("email"))
        return True
    
    @staticmethod
    def log_audit(user_id: str, action: str, details: dict = None, ip_address: str = None):
        """Registra uma ação de auditoria"""
        db = get_db()
        
        audit_log = {
            "user_id": user_id,
            "action": action,
            "details": details or {},
            "ip_address": ip_address,
            "timestamp": AuthService.utcnow(),
        }
        
        db.audit_logs.insert_one(audit_log)
        logger.info("✓ Auditoria registrada: %s para usuário: %s", action, user_id)
