"""Gmail API client wrapper (module 3). google-api-python-client is imported
lazily so the app boots without it installed (CI, tests)."""


def build_gmail(access_token: str):
    """Return an authorized Gmail service client."""
    from google.oauth2.credentials import Credentials  # lazy: heavy SDK
    from googleapiclient.discovery import build  # lazy: heavy SDK

    return build("gmail", "v1", credentials=Credentials(token=access_token), cache_discovery=False)
