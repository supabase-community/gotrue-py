from supabase_auth.errors import AuthError

from .clients import (
    auth_client_with_session,
    client_api_auto_confirm_disabled_client,
    client_api_auto_confirm_off_signups_enabled_client,
    service_role_api_client,
)
from .utils import (
    create_new_user_with_email,
    mock_app_metadata,
    mock_user_credentials,
    mock_user_metadata,
    mock_verification_otp,
)


def test_create_user_should_create_a_new_user():
    credentials = mock_user_credentials()
    response = create_new_user_with_email(email=credentials.get("email"))
    assert response.email == credentials.get("email")


def test_create_user_with_user_metadata():
    user_metadata = mock_user_metadata()
    credentials = mock_user_credentials()
    response = service_role_api_client().create_user(
        {
            "email": credentials.get("email"),
            "password": credentials.get("password"),
            "user_metadata": user_metadata,
        }
    )
    assert response.user.email == credentials.get("email")
    assert response.user.user_metadata == user_metadata
    assert "profile_image" in response.user.user_metadata


def test_create_user_with_app_metadata():
    app_metadata = mock_app_metadata()
    credentials = mock_user_credentials()
    response = service_role_api_client().create_user(
        {
            "email": credentials.get("email"),
            "password": credentials.get("password"),
            "app_metadata": app_metadata,
        }
    )
    assert response.user.email == credentials.get("email")
    assert "provider" in response.user.app_metadata
    assert "providers" in response.user.app_metadata


def test_create_user_with_user_and_app_metadata():
    user_metadata = mock_user_metadata()
    app_metadata = mock_app_metadata()
    credentials = mock_user_credentials()
    response = service_role_api_client().create_user(
        {
            "email": credentials.get("email"),
            "password": credentials.get("password"),
            "user_metadata": user_metadata,
            "app_metadata": app_metadata,
        }
    )
    assert response.user.email == credentials.get("email")
    assert "profile_image" in response.user.user_metadata
    assert "provider" in response.user.app_metadata
    assert "providers" in response.user.app_metadata


def test_list_users_should_return_registered_users():
    credentials = mock_user_credentials()
    create_new_user_with_email(email=credentials.get("email"))
    users = service_role_api_client().list_users()
    assert users
    emails = [user.email for user in users]
    assert emails
    assert credentials.get("email") in emails


def test_get_user_fetches_a_user_by_their_access_token():
    credentials = mock_user_credentials()
    auth_client_with_session_current_user = auth_client_with_session()
    response = auth_client_with_session_current_user.sign_up(
        {
            "email": credentials.get("email"),
            "password": credentials.get("password"),
        }
    )
    assert response.session
    response = auth_client_with_session_current_user.get_user()
    assert response.user.email == credentials.get("email")


def test_get_user_by_id_should_a_registered_user_given_its_user_identifier():
    credentials = mock_user_credentials()
    user = create_new_user_with_email(email=credentials.get("email"))
    assert user.id
    response = service_role_api_client().get_user_by_id(user.id)
    assert response.user.email == credentials.get("email")


def test_modify_email_using_update_user_by_id():
    credentials = mock_user_credentials()
    user = create_new_user_with_email(email=credentials.get("email"))
    response = service_role_api_client().update_user_by_id(
        user.id,
        {
            "email": f"new_{user.email}",
        },
    )
    assert response.user.email == f"new_{user.email}"


def test_modify_user_metadata_using_update_user_by_id():
    credentials = mock_user_credentials()
    user = create_new_user_with_email(email=credentials.get("email"))
    user_metadata = {"favorite_color": "yellow"}
    response = service_role_api_client().update_user_by_id(
        user.id,
        {
            "user_metadata": user_metadata,
        },
    )
    assert response.user.email == user.email
    assert response.user.user_metadata == user_metadata


def test_modify_app_metadata_using_update_user_by_id():
    credentials = mock_user_credentials()
    user = create_new_user_with_email(email=credentials.get("email"))
    app_metadata = {"roles": ["admin", "publisher"]}
    response = service_role_api_client().update_user_by_id(
        user.id,
        {
            "app_metadata": app_metadata,
        },
    )
    assert response.user.email == user.email
    assert "roles" in response.user.app_metadata


def test_modify_confirm_email_using_update_user_by_id():
    credentials = mock_user_credentials()
    response = client_api_auto_confirm_off_signups_enabled_client().sign_up(
        {
            "email": credentials.get("email"),
            "password": credentials.get("password"),
        }
    )
    assert response.user
    assert not response.user.confirmed_at
    response = service_role_api_client().update_user_by_id(
        response.user.id,
        {
            "email_confirm": True,
        },
    )
    assert response.user.confirmed_at


def test_delete_user_should_be_able_delete_an_existing_user():
    credentials = mock_user_credentials()
    user = create_new_user_with_email(email=credentials.get("email"))
    service_role_api_client().delete_user(user.id)
    users = service_role_api_client().list_users()
    emails = [user.email for user in users]
    assert credentials.get("email") not in emails


def test_generate_link_supports_sign_up_with_generate_confirmation_signup_link():
    credentials = mock_user_credentials()
    redirect_to = "http://localhost:9999/welcome"
    user_metadata = {"status": "alpha"}
    response = service_role_api_client().generate_link(
        {
            "type": "signup",
            "email": credentials.get("email"),
            "password": credentials.get("password"),
            "options": {
                "data": user_metadata,
                "redirect_to": redirect_to,
            },
        },
    )
    assert response.user.user_metadata == user_metadata


def test_generate_link_supports_updating_emails_with_generate_email_change_links():  # noqa: E501
    credentials = mock_user_credentials()
    user = create_new_user_with_email(email=credentials.get("email"))
    assert user.email
    assert user.email == credentials.get("email")
    credentials = mock_user_credentials()
    redirect_to = "http://localhost:9999/welcome"
    response = service_role_api_client().generate_link(
        {
            "type": "email_change_current",
            "email": user.email,
            "new_email": credentials.get("email"),
            "options": {
                "redirect_to": redirect_to,
            },
        },
    )
    assert response.user.new_email == credentials.get("email")


def test_invite_user_by_email_creates_a_new_user_with_an_invited_at_timestamp():
    credentials = mock_user_credentials()
    redirect_to = "http://localhost:9999/welcome"
    user_metadata = {"status": "alpha"}
    response = service_role_api_client().invite_user_by_email(
        credentials.get("email"),
        {
            "data": user_metadata,
            "redirect_to": redirect_to,
        },
    )
    assert response.user.invited_at


def test_sign_out_with_an_valid_access_token():
    credentials = mock_user_credentials()
    response = auth_client_with_session().sign_up(
        {
            "email": credentials.get("email"),
            "password": credentials.get("password"),
        },
    )
    assert response.session
    response = service_role_api_client().sign_out(response.session.access_token)


def test_sign_out_with_an_invalid_access_token():
    try:
        service_role_api_client().sign_out("this-is-a-bad-token")
        assert False
    except AuthError:
        pass


def test_verify_otp_with_non_existent_phone_number():
    credentials = mock_user_credentials()
    otp = mock_verification_otp()
    try:
        client_api_auto_confirm_disabled_client().verify_otp(
            {
                "phone": credentials.get("phone"),
                "token": otp,
                "type": "sms",
            },
        )
        assert False
    except AuthError as e:
        assert e.message == "Token has expired or is invalid"


def test_verify_otp_with_invalid_phone_number():
    credentials = mock_user_credentials()
    otp = mock_verification_otp()
    try:
        client_api_auto_confirm_disabled_client().verify_otp(
            {
                "phone": f"{credentials.get('phone')}-invalid",
                "token": otp,
                "type": "sms",
            },
        )
        assert False
    except AuthError as e:
        assert e.message == "Invalid phone number format (E.164 required)"
