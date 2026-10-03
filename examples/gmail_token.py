#!/usr/bin/env python3
"""Create a Gmail token file for inbox-radar, once, in a browser.

    pip install google-api-python-client google-auth google-auth-oauthlib
    python3 examples/gmail_token.py client_secret.json token.json            # read-only, for scan
    python3 examples/gmail_token.py client_secret.json token-modify.json --modify   # for plan approve

`client_secret.json` is an OAuth client of type "Desktop app" from your own Google Cloud project,
with the Gmail API enabled. Keep the read-only and the modify tokens in separate files: the scan
never needs to change anything, so it should not hold a token that can.
"""
import argparse
import os

SCOPES = {"readonly": "https://www.googleapis.com/auth/gmail.readonly",
          "modify": "https://www.googleapis.com/auth/gmail.modify"}


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("client_secret")
    p.add_argument("token")
    p.add_argument("--modify", action="store_true", help="request gmail.modify instead of read-only")
    a = p.parse_args()
    from google_auth_oauthlib.flow import InstalledAppFlow
    scope = SCOPES["modify" if a.modify else "readonly"]
    creds = InstalledAppFlow.from_client_secrets_file(a.client_secret, [scope]).run_local_server(port=0)
    fd = os.open(a.token, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(creds.to_json())
    print(f"wrote {a.token} (scope: {scope.rsplit('/', 1)[1]})")


if __name__ == "__main__":
    main()
