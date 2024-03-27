from typing import Optional

from tekore import Credentials, UserAuth


def get_auth(credentials: Credentials, scope: str, state: Optional[str] = None) -> UserAuth:
    auth = UserAuth(cred=credentials, scope=scope)

    if state is not None:
        auth.state = state

    return auth
