"""Create an admin account.  Run:  python create_admin.py"""
import getpass
import os
import sys
import warnings

import auth
from models import Admin, SessionLocal, init_db
from validation import validate_registration

FIELDS = [
    ("full_name", "Full name"),
    ("email", "Email"),
    ("phone", "Phone number"),
    ("username", "Username"),
]


def hidden_input_works() -> bool:
    """Hidden typing needs a real terminal. PyCharm's Run window is not one: on Windows,
    getpass waits forever there with nothing on screen, which looks like a freeze."""
    return sys.stdin.isatty() and "PYCHARM_HOSTED" not in os.environ


def ask_password(label: str) -> str:
    if hidden_input_works():
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            value = getpass.getpass(f"{label}: ")
        if not any(issubclass(w.category, getpass.GetPassWarning) for w in caught):
            return value
    return input(f"{label}: ")  # visible typing, but it always responds


def main() -> None:
    init_db()
    print("Create an admin account for Karibu Events")
    print("Press Enter after each answer.")
    if hidden_input_works():
        print("Passwords are hidden while you type, so nothing appears - that is normal.\n")
    else:
        print("This window cannot hide typing, so your password will be visible as you type it.\n")

    values = {"terms": "on"}
    to_ask = [name for name, _ in FIELDS] + ["password"]

    with SessionLocal() as db:
        while True:
            for name in to_ask:
                if name == "password":
                    values["password"] = ask_password("Password (min 8 characters, letters + a number)")
                    values["confirm_password"] = ask_password("Confirm password")
                else:
                    label = dict(FIELDS)[name]
                    values[name] = input(f"{label}: ").strip()

            cleaned, errors = validate_registration(values)
            if not errors:
                messages = {"email": "This email is already used.",
                            "phone": "This phone number is already used.",
                            "username": "This username is already taken."}
                errors = {f: messages[f] for f in
                          auth.taken_fields(db, cleaned["email"], cleaned["phone"], cleaned["username"])}
            if not errors:
                break

            print("\nPlease fix the following:")
            for field, message in errors.items():
                print(f"  - {field.replace('_', ' ').capitalize()}: {message}")
            print()
            # Ask again only for the fields that had a problem.
            to_ask = [f for f in errors if f not in ("terms", "confirm_password")]
            if "confirm_password" in errors and "password" not in to_ask:
                to_ask.append("password")

        db.add(Admin(**cleaned, password_hash=auth.hash_password(values["password"])))
        db.commit()
    print(f"\nDone. Admin '{cleaned['username']}' created. You can now log in at /login.")


if __name__ == "__main__":
    main()