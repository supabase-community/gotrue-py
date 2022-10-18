from __future__ import annotations

from base64 import b64decode
from json import loads
from time import time
from typing import Callable, Dict, List, Tuple, Union
from urllib.parse import parse_qs, quote, urlencode, urlparse
from uuid import uuid4

from ..constants import (
    DEFAULT_HEADERS,
    EXPIRY_MARGIN,
    GOTRUE_URL,
    MAX_RETRIES,
    RETRY_INTERVAL,
    STORAGE_KEY,
)
from ..errors import (
    AuthImplicitGrantRedirectError,
    AuthInvalidCredentialsError,
    AuthRetryableError,
    AuthSessionMissingError,
)
from ..helpers import parse_auth_response, parse_user_response
from ..http_clients import SyncClient
from ..timer import Timer
from ..types import (
    AuthChangeEvent,
    AuthResponse,
    OAuthResponse,
    Options,
    Provider,
    Session,
    SignInWithOAuthCredentials,
    SignInWithPasswordCredentials,
    SignInWithPasswordlessCredentials,
    SignUpWithPasswordCredentials,
    Subscription,
    UserAttributes,
    UserResponse,
    VerifyOtpParams,
)
from .gotrue_admin_api import SyncGoTrueAdminAPI
from .gotrue_base_api import SyncGoTrueBaseAPI
from .storage import SyncMemoryStorage, SyncSupportedStorage


class SyncGoTrueClient(SyncGoTrueBaseAPI):
    def __init__(
        self,
        *,
        url: Union[str, None] = None,
        headers: Union[Dict[str, str], None] = None,
        storage_key: Union[str, None] = None,
        auto_refresh_token: bool = True,
        persist_session: bool = True,
        storage: Union[SyncSupportedStorage, None] = None,
        http_client: Union[SyncClient, None] = None,
    ) -> None:
        SyncGoTrueBaseAPI.__init__(
            self,
            url=url or GOTRUE_URL,
            headers=headers or DEFAULT_HEADERS,
            http_client=http_client,
        )
        self._storage_key = storage_key or STORAGE_KEY
        self._auto_refresh_token = auto_refresh_token
        self._persist_session = persist_session
        self._storage = storage or SyncMemoryStorage()
        self._in_memory_session: Union[Session, None] = None
        self._refresh_token_timer: Union[Timer, None] = None
        self._network_retries = 0
        self._state_change_emitters: Dict[str, Subscription] = {}

        self.admin = SyncGoTrueAdminAPI(
            url=self._url,
            headers=self._headers,
            http_client=self._http_client,
        )

    # Initializations

    def initialize(self, *, url: Union[str, None] = None) -> None:
        if url and self._is_implicit_grant_flow(url):
            self.initialize_from_url(url)
        else:
            self.initialize_from_storage()

    def initialize_from_storage(self) -> None:
        return self._recover_and_refresh()

    def initialize_from_url(self, url: str) -> None:
        try:
            if self._is_implicit_grant_flow(url):
                session, redirect_type = self._get_session_from_url(url)
                self._save_session(session)
                self._notify_all_subscribers("SIGNED_IN", session)
                if redirect_type == "recovery":
                    self._notify_all_subscribers("PASSWORD_RECOVERY", session)
        except Exception as e:
            self._remove_session()
            raise e

    # Public methods

    def sign_up(
        self,
        credentials: SignUpWithPasswordCredentials,
    ) -> AuthResponse:
        """
        Creates a new user.
        """
        self._remove_session()
        email = credentials.get("email")
        phone = credentials.get("phone")
        password = credentials.get("password")
        options = credentials.get("options", {})
        redirect_to = options.get("redirect_to")
        data = options.get("data")
        captcha_token = options.get("captcha_token")
        if email:
            response = self._request(
                "POST",
                "signup",
                body={
                    "email": email,
                    "password": password,
                    "data": data,
                    "gotrue_meta_security": {
                        "captcha_token": captcha_token,
                    },
                },
                redirect_to=redirect_to,
                xform=parse_auth_response,
            )
        elif phone:
            response = self._request(
                "POST",
                "signup",
                body={
                    "phone": phone,
                    "password": password,
                    "data": data,
                    "gotrue_meta_security": {
                        "captcha_token": captcha_token,
                    },
                },
                xform=parse_auth_response,
            )
        else:
            raise AuthInvalidCredentialsError(
                "You must provide either an email or phone number and a password"
            )
        if response.session:
            self._save_session(response.session)
            self._notify_all_subscribers("SIGNED_IN", response.session)
        return response

    def sign_in_with_password(
        self,
        credentials: SignInWithPasswordCredentials,
    ) -> AuthResponse:
        """
        Log in an existing user with an email or phone and password.
        """
        self._remove_session()
        email = credentials.get("email")
        phone = credentials.get("phone")
        password = credentials.get("password")
        options = credentials.get("options", {})
        captcha_token = options.get("captcha_token")
        if email:
            response = self._request(
                "POST",
                "token",
                body={
                    "email": email,
                    "password": password,
                    "gotrue_meta_security": {
                        "captcha_token": captcha_token,
                    },
                },
                query={
                    "grant_type": "password",
                },
                xform=parse_auth_response,
            )
        elif phone:
            response = self._request(
                "POST",
                "token",
                body={
                    "phone": phone,
                    "password": password,
                    "gotrue_meta_security": {
                        "captcha_token": captcha_token,
                    },
                },
                query={
                    "grant_type": "password",
                },
                xform=parse_auth_response,
            )
        else:
            raise AuthInvalidCredentialsError(
                "You must provide either an email or phone number and a password"
            )
        if response.session:
            self._save_session(response.session)
            self._notify_all_subscribers("SIGNED_IN", response.session)
        return response

    def sign_in_with_oauth(
        self,
        credentials: SignInWithOAuthCredentials,
    ) -> OAuthResponse:
        """
        Log in an existing user via a third-party provider.
        """
        self._remove_session()
        provider = credentials.get("provider")
        options = credentials.get("options", {})
        redirect_to = options.get("redirect_to")
        scopes = options.get("scopes")
        params = options.get("query_params", {})
        if redirect_to:
            params["redirect_to"] = redirect_to
        if scopes:
            params["scopes"] = scopes
        url = self._get_url_for_provider(provider, params)
        return OAuthResponse(provider=provider, url=url)

    def sign_in_with_otp(
        self,
        credentials: SignInWithPasswordlessCredentials,
    ) -> AuthResponse:
        """
        Log in a user using magiclink or a one-time password (OTP).

        If the `{{ .ConfirmationURL }}` variable is specified in
        the email template, a magiclink will be sent.

        If the `{{ .Token }}` variable is specified in the email
        template, an OTP will be sent.

        If you're using phone sign-ins, only an OTP will be sent.
        You won't be able to send a magiclink for phone sign-ins.
        """
        self._remove_session()
        email = credentials.get("email")
        phone = credentials.get("phone")
        options = credentials.get("options", {})
        email_redirect_to = options.get("email_redirect_to")
        should_create_user = options.get("create_user", True)
        data = options.get("data")
        captcha_token = options.get("captcha_token")
        if email:
            return self._request(
                "POST",
                "otp",
                body={
                    "email": email,
                    "data": data,
                    "create_user": should_create_user,
                    "gotrue_meta_security": {
                        "captcha_token": captcha_token,
                    },
                },
                redirect_to=email_redirect_to,
                xform=parse_auth_response,
            )
        if phone:
            return self._request(
                "POST",
                "otp",
                body={
                    "phone": phone,
                    "data": data,
                    "create_user": should_create_user,
                    "gotrue_meta_security": {
                        "captcha_token": captcha_token,
                    },
                },
                xform=parse_auth_response,
            )
        raise AuthInvalidCredentialsError(
            "You must provide either an email or phone number"
        )

    def verify_otp(self, params: VerifyOtpParams) -> AuthResponse:
        """
        Log in a user given a User supplied OTP received via mobile.
        """
        self._remove_session()
        response = self._request(
            "POST",
            "verify",
            body={
                "gotrue_meta_security": {
                    "captcha_token": params.get("options", {}).get("captcha_token"),
                },
                **params,
            },
            redirect_to=params.get("options", {}).get("redirect_to"),
            xform=parse_auth_response,
        )
        if response.session:
            self._save_session(response.session)
            self._notify_all_subscribers("SIGNED_IN", response.session)
        return response

    def get_session(self) -> Union[Session, None]:
        """
        Returns the session, refreshing it if necessary.

        The session returned can be null if the session is not detected which
        can happen in the event a user is not signed-in or has logged out.
        """
        current_session: Union[Session, None] = None
        if self._persist_session:
            maybe_session = self._storage.get_item(self._storage_key)
            current_session = self._get_valid_session(maybe_session)
            if not current_session:
                self._remove_session()
        else:
            current_session = self._in_memory_session
        if not current_session:
            return None
        has_expired = (
            current_session.expires_at <= time()
            if current_session.expires_at
            else False
        )
        if not has_expired:
            return current_session
        return self._call_refresh_token(current_session.refresh_token)

    def get_user(self, jwt: Union[str, None] = None) -> UserResponse:
        """
        Gets the current user details if there is an existing session.

        Takes in an optional access token `jwt`. If no `jwt` is provided,
        `get_user()` will attempt to get the `jwt` from the current session.
        """
        if not jwt:
            session = self.get_session()
            if session:
                jwt = session.access_token
        return self._request("GET", "user", jwt=jwt, xform=parse_user_response)

    def update_user(self, attributes: UserAttributes) -> UserResponse:
        """
        Updates user data, if there is a logged in user.
        """
        session = self.get_session()
        if not session:
            raise AuthSessionMissingError()
        response = self._request(
            "PUT",
            "user",
            body=attributes,
            jwt=session.access_token,
            xform=parse_user_response,
        )
        session.user = response.user
        self._save_session(session)
        self._notify_all_subscribers("USER_UPDATED", session)
        return response

    def set_session(self, access_token: str, refresh_token: str) -> AuthResponse:
        """
        Sets the session data from the current session. If the current session
        is expired, `set_session` will take care of refreshing it to obtain a
        new session.

        If the refresh token in the current session is invalid and the current
        session has expired, an error will be thrown.

        If the current session does not contain at `expires_at` field,
        `set_session` will use the exp claim defined in the access token.

        The current session that minimally contains an access token,
        refresh token and a user.
        """
        time_now = round(time())
        expires_at = time_now
        has_expired = True
        session: Union[Session, None] = None
        if access_token and access_token.split(".")[1]:
            json_raw = b64decode(access_token.split(".")[1] + "===").decode("utf-8")
            payload = loads(json_raw)
            if payload.get("exp"):
                expires_at = int(payload.get("exp"))
                has_expired = expires_at <= time_now
        if has_expired:
            if not refresh_token:
                raise AuthSessionMissingError()
            response = self._refresh_access_token(refresh_token)
            if not response.session:
                return AuthResponse()
            session = response.session
        else:
            response = self.get_user(access_token)
            session = Session(
                access_token=access_token,
                refresh_token=refresh_token,
                user=response.user,
                token_type="bearer",
                expires_in=expires_at - time_now,
                expires_at=expires_at,
            )
        self._save_session(session)
        self._notify_all_subscribers("TOKEN_REFRESHED", session)
        return AuthResponse(session=session, user=response.user)

    def sign_out(self) -> None:
        """
        Inside a browser context, `sign_out` will remove the logged in user from the
        browser session and log them out - removing all items from localstorage and
        then trigger a `"SIGNED_OUT"` event.

        For server-side management, you can revoke all refresh tokens for a user by
        passing a user's JWT through to `api.sign_out`.

        There is no way to revoke a user's access token jwt until it expires.
        It is recommended to set a shorter expiry on the jwt for this reason.
        """
        session = self.get_session()
        access_token = session.access_token if session else None
        if access_token:
            self.admin.sign_out(access_token)
        self._remove_session()
        self._notify_all_subscribers("SIGNED_OUT", None)

    def on_auth_state_change(
        self,
        callback: Callable[[AuthChangeEvent, Union[Session, None]], None],
    ) -> Subscription:
        """
        Receive a notification every time an auth event happens.
        """
        unique_id = str(uuid4())

        def _unsubscribe() -> None:
            self._state_change_emitters.pop(unique_id)

        subscription = Subscription(
            id=unique_id,
            callback=callback,
            unsubscribe=_unsubscribe,
        )
        self._state_change_emitters[unique_id] = subscription
        return subscription

    def reset_password_email(
        self,
        email: str,
        options: Options = {},
    ) -> None:
        """
        Sends a password reset request to an email address.
        """
        raise NotImplementedError

    # Private methods

    def _remove_session(self) -> None:
        if self._persist_session:
            self._storage.remove_item(self._storage_key)
        else:
            self._in_memory_session = None
        if self._refresh_token_timer:
            self._refresh_token_timer.cancel()
            self._refresh_token_timer = None

    def _get_session_from_url(
        self,
        url: str,
    ) -> Tuple[Session, Union[str, None]]:
        if not self._is_implicit_grant_flow(url):
            raise AuthImplicitGrantRedirectError("Not a valid implicit grant flow url.")
        result = urlparse(url)
        params = parse_qs(result.query)
        error_description = self._get_param(params, "error_description")
        if error_description:
            error_code = self._get_param(params, "error_code")
            error = self._get_param(params, "error")
            if not error_code:
                raise AuthImplicitGrantRedirectError("No error_code detected.")
            if not error:
                raise AuthImplicitGrantRedirectError("No error detected.")
            raise AuthImplicitGrantRedirectError(
                error_description,
                {"code": error_code, "error": error},
            )
        provider_token = self._get_param(params, "provider_token")
        provider_refresh_token = self._get_param(params, "provider_refresh_token")
        access_token = self._get_param(params, "access_token")
        if not access_token:
            raise AuthImplicitGrantRedirectError("No access_token detected.")
        expires_in = self._get_param(params, "expires_in")
        if not expires_in:
            raise AuthImplicitGrantRedirectError("No expires_in detected.")
        refresh_token = self._get_param(params, "refresh_token")
        if not refresh_token:
            raise AuthImplicitGrantRedirectError("No refresh_token detected.")
        token_type = self._get_param(params, "token_type")
        if not token_type:
            raise AuthImplicitGrantRedirectError("No token_type detected.")
        time_now = round(time())
        expires_at = time_now + int(expires_in)
        user = self.get_user(access_token)
        session = Session(
            provider_token=provider_token,
            provider_refresh_token=provider_refresh_token,
            access_token=access_token,
            expires_in=int(expires_in),
            expires_at=expires_at,
            refresh_token=refresh_token,
            token_type=token_type,
            user=user.user,
        )
        redirect_type = self._get_param(params, "type")
        return session, redirect_type

    def _recover_and_refresh(self) -> None:
        raw_session = self._storage.get_item(self._storage_key)
        current_session = self._get_valid_session(raw_session)
        if not current_session:
            if raw_session:
                self._remove_session()
            return
        time_now = round(time())
        expires_at = current_session.expires_at
        if expires_at and expires_at < time_now + EXPIRY_MARGIN:
            refresh_token = current_session.refresh_token
            if self._auto_refresh_token and refresh_token:
                self._network_retries += 1
                try:
                    self._call_refresh_token(refresh_token)
                    self._network_retries = 0
                except Exception as e:
                    if (
                        isinstance(e, AuthRetryableError)
                        and self._network_retries < MAX_RETRIES
                    ):
                        if self._refresh_token_timer:
                            self._refresh_token_timer.cancel()
                        self._refresh_token_timer = Timer(
                            (RETRY_INTERVAL ** (self._network_retries * 100)),
                            self._recover_and_refresh,
                        )
                        self._refresh_token_timer.start()
                        return
            self._remove_session()
            return
        if self._persist_session:
            self._save_session(current_session)
        self._notify_all_subscribers("SIGNED_IN", current_session)

    def _call_refresh_token(self, refresh_token: str) -> Session:
        if not refresh_token:
            raise AuthSessionMissingError()
        response = self._refresh_access_token(refresh_token)
        if not response.session:
            raise AuthSessionMissingError()
        self._save_session(response.session)
        self._notify_all_subscribers("TOKEN_REFRESHED", response.session)
        return response.session

    def _refresh_access_token(self, refresh_token: str) -> AuthResponse:
        return self._request(
            "POST",
            "token",
            query={"grant_type": "refresh_token"},
            body={"refresh_token": refresh_token},
            xform=parse_auth_response,
        )

    def _save_session(self, session: Session) -> None:
        if not self._persist_session:
            self._in_memory_session = session
        expire_at = session.expires_at
        if expire_at:
            pass
            time_now = round(time())
            expire_in = expire_at - time_now
            refresh_duration_before_expires = (
                EXPIRY_MARGIN if expire_in > EXPIRY_MARGIN else 0.5
            )
            value = (expire_in - refresh_duration_before_expires) * 1000
            self._start_auto_refresh_token(value)
        if self._persist_session and session.expires_at:
            self._storage.set_item(self._storage_key, session.json())

    def _start_auto_refresh_token(self, value: float) -> None:
        if self._refresh_token_timer:
            self._refresh_token_timer.cancel()
            self._refresh_token_timer = None
        if value <= 0 or not self._auto_refresh_token:
            return

        def refresh_token_function():
            self._network_retries += 1
            try:
                session = self.get_session()
                if session:
                    self._call_refresh_token(session.refresh_token)
                    self._network_retries = 0
            except Exception as e:
                if (
                    isinstance(e, AuthRetryableError)
                    and self._network_retries < MAX_RETRIES
                ):
                    self._start_auto_refresh_token(
                        RETRY_INTERVAL ** (self._network_retries * 100)
                    )

        self._refresh_token_timer = Timer(value, refresh_token_function)
        self._refresh_token_timer.start()

    def _notify_all_subscribers(
        self,
        event: AuthChangeEvent,
        session: Union[Session, None],
    ) -> None:
        for subscription in self._state_change_emitters.values():
            subscription.callback(event, session)

    def _get_valid_session(
        self,
        raw_session: Union[str, None],
    ) -> Union[Session, None]:
        if not raw_session:
            return None
        data = loads(raw_session)
        if not data:
            return None
        if not data.get("access_token"):
            return None
        if not data.get("refresh_token"):
            return None
        if not data.get("expires_at"):
            return None
        try:
            expires_at = int(data["expires_at"])
            data["expires_at"] = expires_at
        except ValueError:
            return None
        try:
            session = Session.parse_obj(data)
            return session
        except Exception:
            return None

    def _get_param(
        self,
        query_params: Dict[str, List[str]],
        name: str,
    ) -> Union[str, None]:
        if name in query_params:
            return query_params[name][0]
        return None

    def _is_implicit_grant_flow(self, url: str) -> bool:
        result = urlparse(url)
        params = parse_qs(result.query)
        return "access_token" in params or "error_description" in params

    def _get_url_for_provider(
        self,
        provider: Provider,
        params: Dict[str, str],
    ) -> str:
        params = {k: quote(v) for k, v in params.items()}
        params["provider"] = quote(provider)
        query = urlencode(params)
        return f"{self._url}/authorize?{query}"


def test():
    client = SyncGoTrueClient()
    client.initialize()