
import re
from datetime import datetime

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.]{3,30}$")


def normalize_phone(raw: str) -> str:

    raw = raw.strip()
    plus = "+" if raw.startswith("+") else ""
    return plus + re.sub(r"\D", "", raw)


def _check_details(form: dict, errors: dict) -> dict:

    full_name = " ".join(form.get("full_name", "").split())
    if len(full_name) < 3 or " " not in full_name:
        errors["full_name"] = "Enter your full name (first and last name)."
    elif len(full_name) > 100:
        errors["full_name"] = "Name is too long."

    email = form.get("email", "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        errors["email"] = "Enter a valid email address."

    phone = normalize_phone(form.get("phone", ""))
    if not re.fullmatch(r"\+?\d{9,15}", phone):
        errors["phone"] = "Enter a valid phone number, e.g. +255 712 345 678."


    return {"full_name": full_name, "email": email, "phone": phone}

def _check_details2(form: dict, errors: dict) -> dict:
    title = " ".join(form.get("title", "").split())
    if title =='':
        errors["title"] = "Title is required."
    elif len(title) > 50 :
        errors["title"] = "Enter a title with  less 50 characters."

    type = " ".join(form.get("type", "").split())
    if type=='':
        errors["type"] = "Type is required."
    elif len(type) > 50:
        errors["type"] = "Enter a type at most 50 characters."
    date_time = " ".join(form.get("date_time", "").split())
    if date_time=='':
        errors["date_time"] = "Enter a date time it is required."
    elif date_time:
        try:
            date=datetime.strptime(date_time, "%Y-%m-%dT%H:%M")
            if date<=datetime.now():
                errors["date_time"] = "Enter a current or future date."
        except ValueError:
            errors["date_time"] = "Enter a valid date time."

    venue = " ".join(form.get("venue", "").split())
    if venue=='':
        errors["venue"] = "Venue is required."
    elif len(venue) > 90 :
        errors["venue"] = "Enter a venue at most 90 characters."
    description = " ".join(form.get("description", "").split())
    if len(description) > 400 :
        errors["description"] = "Enter a description at most 400 characters."

    return {"title": title, "type": type, "date_time": date_time, "venue": venue,
            "description": description}

def password_problem(password: str) -> str:

    if len(password) < 8:
        return f"Use at least 8 characters the  password has {len(password)} characters."
    if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        return "Include at least one letter and one number."
    if len(password) > 128:
        return "Password is too long."
    return ""


def validate_registration(form: dict):

    errors = {}
    cleaned = _check_details(form, errors)

    username = form.get("username", "").strip()
    if not USERNAME_RE.match(username):
        errors["username"] = "3 to 30 characters: letters, numbers, dot or underscore."
    cleaned["username"] = username

    password = form.get("password", "")
    problem = password_problem(password)
    if problem:
        errors["password"] = problem
    elif form.get("confirm_password", "") != password:
        errors["confirm_password"] = "Passwords do not match."

    if not form.get("terms"):
        errors["terms"] = "You must accept the terms to create an account."

    return cleaned, errors

def validate_event(form:dict):
    errors={}
    cleaned=_check_details2(form, errors)
    event_key = form.get("event_key", "")
    problem = password_problem(event_key)
    if problem:
        errors["event_key"] = problem
    elif form.get("confirm_event_key", "") != event_key:
        errors["confirm_event_key"] = "event key do not match."

    return cleaned, errors

def validate_profile(form: dict):
    errors = {}
    cleaned = _check_details(form, errors)
    return cleaned, errors


def validate_password_change(form: dict, current_ok: bool):

    errors = {}
    new = form.get("new_password", "")

    if not form.get("current_password"):
        errors["current_password"] = "Enter your current password."
    elif not current_ok:
        errors["current_password"] = "Your current password is incorrect."

    problem = password_problem(new)
    if problem:
        errors["new_password"] = problem
    elif new == form.get("current_password"):
        errors["new_password"] = "Choose a password different from your current one."
    elif form.get("confirm_password", "") != new:
        errors["confirm_password"] = "Passwords do not match."

    return errors