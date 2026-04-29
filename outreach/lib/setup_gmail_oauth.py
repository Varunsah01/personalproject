"""One-time OAuth setup for a Gmail sending inbox.

Usage:
    python outreach/lib/setup_gmail_oauth.py \\
        --client-secret path/to/client_secret.json \\
        --inbox-address varun.outreach1@gmail.com

This opens a browser for Google OAuth consent with the ``gmail.send``
scope only.  The resulting token (including refresh_token for offline
access) is saved to ``secrets/{inbox_address}.token.json``.

Prerequisites:
    1. Go to https://console.cloud.google.com/
    2. Create a project (or reuse one)
    3. Enable the Gmail API: APIs & Services → Library → Gmail API → Enable
    4. Create OAuth credentials: APIs & Services → Credentials → Create →
       OAuth client ID → Desktop app
    5. Download the client_secret.json file
    6. Run this script once per inbox
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

# Only request send permission — no read, no delete, no modify
_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def setup(client_secret_path: Path, inbox_address: str) -> Path:
    """Run the OAuth consent flow and save the token.

    Args:
        client_secret_path: Path to the downloaded client_secret.json.
        inbox_address: Gmail address this token is for.

    Returns:
        Path to the saved token file.
    """
    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secret_path),
        scopes=_SCOPES,
    )
    # access_type="offline" ensures we get a refresh_token
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )

    secrets_dir = Path("secrets")
    secrets_dir.mkdir(exist_ok=True)
    token_path = secrets_dir / f"{inbox_address}.token.json"

    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or _SCOPES),
    }
    token_path.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
    return token_path


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Set up Gmail OAuth for an outreach sending inbox"
    )
    parser.add_argument(
        "--client-secret", type=Path, required=True,
        help="Path to client_secret.json from Google Cloud Console"
    )
    parser.add_argument(
        "--inbox-address", type=str, required=True,
        help="Gmail address to authenticate (e.g. varun.outreach1@gmail.com)"
    )

    args = parser.parse_args()

    if not args.client_secret.exists():
        print(f"Error: {args.client_secret} not found", file=sys.stderr)
        sys.exit(1)

    token_path = setup(args.client_secret, args.inbox_address)
    print(f"\nToken saved to: {token_path}")
    print(f"\nNext steps:")
    print(f"  1. Add to .env.outreach:")
    print(f"     OUTREACH_INBOX_N_ADDRESS={args.inbox_address}")
    print(f"     OUTREACH_INBOX_N_TOKEN_PATH={token_path}")
    print(f"  2. Repeat for each inbox you want in the rotation pool")
    print(f"  3. Run: python outreach/lib/sender.py --dry-run")


if __name__ == "__main__":
    main()
