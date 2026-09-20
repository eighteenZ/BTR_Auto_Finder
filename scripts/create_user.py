"""User management for the corporate-mailbox account system.

Examples:
    # First admin (mailbox credentials verified via IMAP before creation)
    python scripts/create_user.py --admin --email wendy@btrlgts.com

    # Skip IMAP verification (mail server unreachable; account still active)
    python scripts/create_user.py --admin --email wendy@btrlgts.com --no-verify

    # Add a member / list users / reset a key / disable an account
    python scripts/create_user.py --email ops@btrlgts.com
    python scripts/create_user.py --list
    python scripts/create_user.py --reset-key --email ops@btrlgts.com
    python scripts/create_user.py --disable --email ops@btrlgts.com
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api.auth as auth
from persistence.db import execute, get_session


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", help="corporate mailbox address")
    parser.add_argument("--password", help="mailbox password (omit to be prompted; used only for IMAP verification)")
    parser.add_argument("--admin", action="store_true", help="create with the admin role")
    parser.add_argument("--no-verify", action="store_true",
                        help="skip IMAP credential check (for when EMAIL_IMAP_HOST is unreachable)")
    parser.add_argument("--list", action="store_true", help="list users")
    parser.add_argument("--reset-key", action="store_true", help="rotate a user's API key")
    parser.add_argument("--disable", action="store_true", help="deactivate a user")
    args = parser.parse_args()

    if args.list:
        for u in auth.list_users():
            print(f"{u['email']:<36} role={u['role']:<7} active={u['active']} "
                  f"key={u['api_key'][:12]}… last_login={u['last_login_at'] or '-'}")
        return 0

    if args.reset_key or args.disable:
        if not args.email:
            parser.error("--email is required")
        if args.reset_key:
            user = auth.reset_api_key(args.email)
            if not user:
                print(f"✗ {args.email} not found")
                return 1
            print(f"new api_key: {user['api_key']}")
        if args.disable:
            with get_session() as session:
                execute(session, "UPDATE users SET active = false WHERE email = ?", (args.email.strip().lower(),))
            print(f"{args.email} disabled")
        return 0

    if not args.email:
        parser.error("--email is required (or use --list)")

    password = args.password
    if password is None and not args.no_verify:
        password = getpass.getpass(f"Mailbox password for {args.email} (IMAP verification): ")

    try:
        user = auth.create_user(
            args.email,
            role="admin" if args.admin else "member",
            verify_password=None if args.no_verify else password,
        )
    except ConnectionError as exc:
        print(f"✗ mail server unreachable: {exc}")
        print("  fix EMAIL_IMAP_HOST/PORT in .env, or re-run with --no-verify")
        return 2
    except ValueError as exc:
        print(f"✗ {exc}")
        return 1

    if not user:
        print("✗ mailbox rejected these credentials; user not created")
        return 1

    print(f"✓ {user['email']} created/updated (role={user['role']})")
    print(f"  api_key: {user['api_key']}")
    print("  登录：/login 页用企业邮箱+邮箱密码；程序化调用用上面的 api_key（X-API-Key 头）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
