from uuid import uuid4

from app.auth_utils import create_access_token, decode_access_token
from app.services.auth_service import AuthService


def test_hash_password_roundtrip() -> None:
    password = "SenhaSegura123!"
    hashed = AuthService.hash_password(password)

    assert hashed != password
    assert AuthService.verify_password(password, hashed)
    assert not AuthService.verify_password("senha-invalida", hashed)


def test_access_token_roundtrip() -> None:
    token = create_access_token(subject="user-test", role="admin")
    payload = decode_access_token(token)

    assert payload["sub"] == "user-test"
    assert payload["role"] == "admin"
    assert payload["type"] == "access"


def test_auth_register_login_and_me(client) -> None:
    email = f"auth-{uuid4().hex[:10]}@example.com"
    register_response = client.post(
        "/api/auth/register",
        json={
            "name": "Usuario Teste",
            "email": email,
            "phone": "+55 11 99999-0000",
            "password": "SenhaSegura123!",
        },
    )
    assert register_response.status_code == 201, register_response.text

    token = register_response.json()["access_token"]

    login_response = client.post(
        "/api/auth/login",
        json={
            "email": email,
            "password": "SenhaSegura123!",
        },
    )
    assert login_response.status_code == 200, login_response.text

    me_response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_response.status_code == 200, me_response.text
    payload = me_response.json()
    assert payload["email"] == email
    assert payload["name"] == "Usuario Teste"


def test_auth_update_profile_and_change_password(client) -> None:
    email = f"profile-{uuid4().hex[:10]}@example.com"
    register_response = client.post(
        "/api/auth/register",
        json={
            "name": "Perfil Original",
            "email": email,
            "phone": "+55 11 98888-0000",
            "password": "SenhaSegura123!",
        },
    )
    assert register_response.status_code == 201, register_response.text

    token = register_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    patch_response = client.patch(
        "/api/auth/me",
        headers=headers,
        json={
            "name": "Perfil Atualizado",
            "username": "perfil.atualizado",
        },
    )
    assert patch_response.status_code == 200, patch_response.text
    patched = patch_response.json()
    assert patched["name"] == "Perfil Atualizado"
    assert patched["username"] == "perfil.atualizado"

    change_password_response = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={
            "current_password": "SenhaSegura123!",
            "new_password": "SenhaNova456!",
        },
    )
    assert change_password_response.status_code == 200, change_password_response.text

    old_login_response = client.post(
        "/api/auth/login",
        json={
            "email": email,
            "password": "SenhaSegura123!",
        },
    )
    assert old_login_response.status_code == 401

    new_login_response = client.post(
        "/api/auth/login",
        json={
            "email": email,
            "password": "SenhaNova456!",
        },
    )
    assert new_login_response.status_code == 200, new_login_response.text
