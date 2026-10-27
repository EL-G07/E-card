from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from datetime import datetime

from fastapi import Depends, FastAPI, Form, Request, Body
from fastapi.responses import HTMLResponse, RedirectResponse,StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import func, select, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session



import io
import base64
import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

import auth
from models import User, get_db, init_db, Event, Event_payment, Attendance
from validation import validate_password_change, validate_profile, validate_registration,validate_event

BASE_DIR = Path(__file__).resolve().parent
APP_NAME = "Karibu Events"
_DUMMY_HASH = auth.hash_password("not-a-real-password")  # keeps timing equal for unknown usernames


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # creates karibu.db with the users and admins tables on first start
    yield


app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
DB = Annotated[Session, Depends(get_db)]


def render(request: Request, name: str, status_code: int = 200, **context):
    context["app_name"] = APP_NAME
    # Changes whenever style.css changes, so browsers never keep showing an old copy.
    context["css_v"] = int((BASE_DIR / "static" / "style.css").stat().st_mtime)
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def set_session(response, role: str, account, remember: bool) -> None:
    response.set_cookie(
        auth.COOKIE_NAME,
        auth.make_token(role, account, remember),
        max_age=auth.cookie_max_age(remember),
        httponly=True,
        samesite="lax",  # stops other websites from submitting forms for the user
    )


# ----------------------------------------------------------------------- front page

@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: DB):
    role, account = auth.current_account(request, db)  # None, None when nobody is logged in
    return render(request, "index.html", role=role, account=account)


@app.exception_handler(StarletteHTTPException)
async def friendly_errors(request: Request, exc: StarletteHTTPException):
    """Pages that are not built yet (Create an event, Forgot password...) get a clear page."""
    if exc.status_code == 404:
        return render(request, "404.html", status_code=404)
    return HTMLResponse(f"{exc.status_code} - {exc.detail}", status_code=exc.status_code)


# ----------------------------------------------------------------------- login

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: DB, registered: int = 0,reset: int = 0):
    role, _ = auth.current_account(request, db)
    if role:
        return RedirectResponse("/admin" if role == "admin" else "/dashboard", status_code=303)
    return render(request, "login.html", registered=bool(registered), reset=bool(reset), error="", username="")


@app.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    db: DB,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    remember: Annotated[str, Form()] = "",
):
    username = username.strip()
    role, account = auth.find_by_login(db, username) if username else (None, None)
    password_ok = auth.verify_password(password, account.password_hash if account else _DUMMY_HASH)

    error = ""
    if not username or not password:
        error = "Enter your username and password."
    elif not account or not password_ok:
        error = "Incorrect username or password."
    elif getattr(account, "is_active", True) is False:
        error = "This account has been suspended. Please contact the administrator."
    if error:
        return render(request, "login.html", status_code=401,
                      registered=False, error=error, username=username)

    account.last_login_at = func.now()
    db.commit()

    response = RedirectResponse("/admin" if role == "admin" else "/dashboard", status_code=303)
    set_session(response, role, account, bool(remember))
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.COOKIE_NAME)
    return response


# ----------------------------------------------------------------------- register

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return render(request, "register.html", values={}, errors={})


@app.post("/register", response_class=HTMLResponse)
def register_submit(
    request: Request,
    db: DB,
    full_name: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    phone: Annotated[str, Form()] = "",
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    confirm_password: Annotated[str, Form()] = "",
    terms: Annotated[str, Form()] = "",
):
    form = {"full_name": full_name, "email": email, "phone": phone, "username": username,
            "password": password, "confirm_password": confirm_password, "terms": terms}
    cleaned, errors = validate_registration(form)

    if not errors:
        messages = {
            "email": "An account with this email already exists.",
            "phone": "An account with this phone number already exists.",
            "username": "This username is taken. Please choose another.",
        }
        taken = auth.taken_fields(db, cleaned["email"], cleaned["phone"], cleaned["username"])
        errors = {field: messages[field] for field in taken}

    if not errors:
        try:
            db.add(User(**cleaned, password_hash=auth.hash_password(password)))
            db.commit()
        except IntegrityError:  # the same details were registered a moment ago
            db.rollback()
            errors = {"username": "These details were just registered. Please try again."}

    if errors:
        # Send back what was typed (never the passwords) with the error messages.
        values = {**cleaned, "terms": bool(terms)}
        return render(request, "register.html", status_code=400, values=values, errors=errors)
    return RedirectResponse("/login?registered=1", status_code=303)


# ----------------------------------------------------------------------- admin

def admin_page_response(request: Request, db: Session, admin, q: str = "", message: str = "",
                        kind: str = "success", temp_password: str = "", status_code: int = 200):
    query = select(User).order_by(User.created_at.desc(), User.id.desc())
    if q:
        like = f"%{q}%"
        query = query.where(User.full_name.like(like) | User.username.like(like)
                            | User.email.like(like) | User.phone.like(like))
    total = db.scalar(select(func.count(User.id)))
    suspended = db.scalar(select(func.count(User.id)).where(User.is_active.is_(False)))
    return render(
        request, "admin.html", status_code=status_code, admin=admin, q=q,
        users=db.scalars(query).all(), total=total, suspended=suspended, active=total - suspended,
        message=message, kind=kind, temp_password=temp_password,
    )


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, db: DB, q: str = ""):
    role, admin = auth.current_account(request, db)
    if role is None:
        return RedirectResponse("/login", status_code=303)
    if role != "admin":
        return HTMLResponse("403 - Administrators only.", status_code=403)
    return admin_page_response(request, db, admin, q=q.strip())


@app.post("/admin/users/{user_id}/{action}", response_class=HTMLResponse)
def admin_user_action(request: Request, db: DB, user_id: int, action: str):
    role, admin = auth.current_account(request, db)
    if role is None:
        return RedirectResponse("/login", status_code=303)
    if role != "admin":
        return HTMLResponse("403 - Administrators only.", status_code=403)

    user = db.get(User, user_id)
    if not user:
        return admin_page_response(request, db, admin, message="That user no longer exists.",
                                  kind="error", status_code=404)
    name = f"{user.full_name} ({user.username})"

    if action == "suspend":
        user.is_active = False
        message = f"{name} has been suspended and can no longer log in."
    elif action == "activate":
        user.is_active = True
        message = f"{name} has been re-activated."
    elif action == "reset-password":
        temp = auth.generate_temp_password()
        user.password_hash = auth.hash_password(temp)
        db.commit()
        return admin_page_response(request, db, admin, message=f"Password reset for {name}.",
                                  temp_password=temp)
    elif action == "delete":
        db.delete(user)
        message = f"{name} has been permanently deleted."
    else:
        return admin_page_response(request, db, admin, message="Unknown action.",
                                  kind="error", status_code=400)

    db.commit()
    return admin_page_response(request, db, admin, message=message)


# ----------------------------------------------------------------------- member dashboard

# The three event pages are placeholders for now: (title, what the page will show).
EVENT_PAGES = {
    "created": ("Created events status", "Track the events you created and see where each one stands."),
    "paid": ("Paid invited events", "Events you were invited to and have already paid for."),
    "unpaid": ("Unpaid invited events", "Events you were invited to that still need payment."),
}


def user_or_redirect(request: Request, db: Session):
    """Return (user, None), or (None, a response that sends the visitor elsewhere)."""
    role, account = auth.current_account(request, db)
    if role is None:
        return None, RedirectResponse("/login", status_code=303)
    if role == "admin":
        return None, RedirectResponse("/admin", status_code=303)
    return account, None


def render_dashboard(request: Request, name: str, user, active: str, **context):
    return render(request, name, user=user, active=active, **context)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: DB):
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect

    created_count = db.scalar(
        select(func.count(Event.id)).where(Event.creator_id == user.id)
    )
    paid_count = db.scalar(
        select(func.count(Event_payment.id)).where(
            Event_payment.user == user.username,
            Event_payment.payment.is_(True),
        )
    )
    unpaid_count = db.scalar(
        select(func.count(Event_payment.id)).where(
            Event_payment.user == user.username,
            Event_payment.payment.is_(False),
        )
    )

    return render_dashboard(request, "dashboard.html", user, "overview",
                            created=created_count,
                            paid=paid_count,
                            unpaid=unpaid_count)

# ----------------------------------------------------------------------- event lists (one route per concept)

def _render_event_list(request, db, user, *, active, template, page_title,
                       description, events):
    return render_dashboard(
        request, template, user, active,
        page_title=page_title, description=description, events=events,
    )


@app.get("/dashboard/events/created", response_class=HTMLResponse)
def events_created(request: Request, db: DB):
    """Events this user created (organiser view)."""
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect

    events = (
        db.query(Event)
        .filter(Event.creator_id == user.id)
        .order_by(Event.date_time.desc())
        .all()
    )

    return _render_event_list(
        request, db, user,
        active="created",
        template="dashboard_soon.html",
        page_title="Created events status",
        description="Track the events you created and see where each one stands.",
        events=events,
    )


@app.get("/dashboard/event/paid", response_class=HTMLResponse)
def events_paid(request: Request, db: DB):
    """Events this user joined AND has paid for."""
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect

    tickets = []
    rows = (
        db.query(Event, Event_payment)
        .join(Event_payment, Event_payment.title == Event.title)
        .filter(
            Event_payment.user == user.username,
            Event_payment.payment.is_(True),
        )
        .order_by(Event.date_time.desc())
        .all()
    )
    for event, payment in rows:
        payload = _ticket_payload(event, user, payment)
        img = qrcode.make(payload)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        tickets.append({
            "event": event,
            "payment": payment,
            "qr_data_uri": f"data:image/png;base64,{b64}",
        })

    return render_dashboard(request, "paid_event.html", user, "paid", tickets=tickets)


@app.get("/dashboard/event/unpaid", response_class=HTMLResponse)
def events_unpaid(request: Request, db: DB):
    """Events this user joined but has NOT paid for."""
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect

    tickets = []
    rows = (
        db.query(Event, Event_payment)
        .join(Event_payment, Event_payment.title == Event.title)
        .filter(
            Event_payment.user == user.username,
            Event_payment.payment.is_(False),
        )
        .order_by(Event.date_time.desc())
        .all()
    )
    for event, payment in rows:
        payload = _ticket_payload(event, user, payment)
        img = qrcode.make(payload)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        tickets.append({
            "event": event,
            "payment": payment,
            "qr_data_uri": f"data:image/png;base64,{b64}",
        })

    return render_dashboard(request, "unpaid_event.html", user, "unpaid", tickets=tickets)



@app.post("/dashboard/create/event", response_class=HTMLResponse)
def register_event(
    request: Request,
    db: DB,
    title: Annotated[str, Form()] = "",
    type: Annotated[str, Form()] = "",
    date_time: Annotated[str, Form()] = "",
    venue: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    event_key: Annotated[str, Form()] = "",
    confirm_event_key: Annotated[str, Form()] = "",
    ):
    user,redirect =user_or_redirect(request,db)
    if redirect:
        return redirect
    form = {"title": title, "type": type, "date_time": date_time, "venue": venue,
            "description": description,'event_key': event_key,'confirm_event_key': confirm_event_key}
    cleaned, errors = validate_event(form)
    cleaned["creator_id"] = user.id
    cleaned["date_time"] = datetime.fromisoformat(date_time)
    if not errors:
        try:
            db.add(Event(**cleaned,event_key=auth.hash_password(event_key)))
            db.commit()
        except IntegrityError:
            db.rollback()
            errors = {"title": "These details were just registered. Please try again."}

    if errors:

        values = {**cleaned}
        print('Errors:', errors )
        return render(request, "event_management.html",user=user ,status_code=400, values=values, errors=errors)

    return RedirectResponse("/dashboard/events/created?created=1", status_code=303)




@app.get("/dashboard/create/event", response_class=HTMLResponse)
def event_new_page(request: Request, db: DB):
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect

    return render_dashboard(request, "event_management.html", user, "generate",
                            values={}, errors={})

@app.get("/dashboard/join/event", response_class=HTMLResponse)
def join_event(request: Request, db: DB):
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect

    return render_dashboard(request, "Join.html", user, "join", errors={})

@app.post("/dashboard/join/event", response_class=HTMLResponse)
def join_event(request:Request,
               db: DB,
               title:Annotated[str, Form()]="",
               kind:Annotated[str, Form()]="",
               event_key:Annotated[str, Form()]=""):
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect
    account=db.scalar(select(Event).where(Event.title==title))
    errors={}
    evn=db.scalars(select(Event_payment).where(and_(Event_payment.title==title, Event_payment.user==user.username))).first()

    if not account:
        errors['title']="There is no such kind of event"

    elif not auth.verify_password(event_key, account.event_key):
        errors['event_key'] = 'Wrong event key'

    elif evn:
        errors['title'] = 'You have already joined event with this title'

    if not errors:
        try:
            user1=user.username
            phone=user.phone
            email=user.email
            full_name=user.full_name
            if kind=='Single':
                db.add(Event_payment(title=title, user=user1, phone=phone, email=email, full_name=full_name))
                db.commit()
            else:
                db.add(Event_payment(title=title, user=user1, phone=phone, email=email, full_name=full_name,kind='Double'))
                db.commit()

        except IntegrityError:
            db.rollback()
            errors = {"title": "These details were just registered. Please try again."}
    if errors:
        return render(request, "join.html",user=user ,status_code=400, errors=errors)

    return RedirectResponse('/dashboard/join/event', status_code=303)

@app.get("/dashboard/attendance/event", response_class=HTMLResponse)
def attendance(request: Request, db: DB):
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect
    event=db.scalars(select(Event).where(Event.creator_id==user.id)).all()

    return render_dashboard(request, "attendance.html", user,"attendance",event=event,values={},event_2="", errors={},title=None)

@app.post("/dashboard/attendance/event", response_class=HTMLResponse)
def attend(
        request: Request,
        db: DB,
        title:Annotated[str, Form()]="",
):
    user,redirect = user_or_redirect(request, db)
    if redirect:
        return redirect
    event_2=db.scalars(select(Event_payment).where(Event_payment.title==title)).all()
    errors={}
    if event_2:
        return render_dashboard(request, "attendance2.html", user, "attendance", event_2=event_2, title=title,
                                values={}, errors={})

    else:
        errors['type'] = 'There is no such kind of event'
        return render_dashboard(request, "attendance.html", user, "attendance", values={}, event_2="", errors=errors,
                                title=None)

@app.get("/dashboard/create/special", response_class=HTMLResponse)
def create_special(request: Request, db: DB):
    user, redirect=user_or_redirect(request,db)
    if redirect:
        return redirect
    event = db.scalars(select(Event).where(Event.creator_id == user.id)).all()
    return render_dashboard(request,'special.html',user,'special', event=event, errors={})

@app.post("/dashboard/create/special", response_class=HTMLResponse)
def create_special(
        request: Request,
        db: DB,
        title:Annotated[str, Form()]="",
        full_name:Annotated[str,Form()]="",
        event_key:Annotated[str,Form()]="",
        kind:Annotated[str,Form()]=""
        ):
    user, redirect=user_or_redirect(request,db)
    if redirect:
        return redirect
    account = db.scalar(select(Event).where(Event.title == title))
    errors = {}
    evn = db.scalars(
        select(Event_payment).where(and_(Event_payment.title == title, Event_payment.full_name == user.full_name))).first()

    if not account:
        errors['title'] = "There is no such kind of event"

    elif not auth.verify_password(event_key, account.event_key):
        errors['event_key'] = 'Wrong event key'


    existing=db.query(Event_payment).filter((Event_payment.full_name==full_name)&(Event_payment.email==user.email)&(Event_payment.title==title)).first()
    if not errors:
        if  existing is None:
            user1=user.username
            phone=user.phone
            email=user.email
            if kind=='Single':
                db.add(Event_payment(title=title, user=user1, phone=phone, email=email, full_name=full_name,payment=True))
                db.commit()
            else:
                db.add(Event_payment(title=title, user=user1, phone=phone, email=email, full_name=full_name,kind='Double',payment=True))
                db.commit()

        else:
            errors = {"title": "These details were just registered. Please try again."}
            print(full_name,user.email,title)

    if errors:
        return render(request, "special.html",user=user ,status_code=400, errors=errors)

    return RedirectResponse('/dashboard/create/special', status_code=303)

@app.post("/dashboard/tickets/paid/{event_id}", response_class=HTMLResponse)
def ticket_rows(request: Request, db: DB, event_id: int):        # ← DB, not Session
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect

    payment = db.scalars(
        select(Event_payment).where(Event_payment.id == event_id)
    ).first()
    event_2=db.scalars(
        select(Event_payment).where(Event_payment.id == event_id)
    ).all()
    if not payment:
        return render(request, "404.html", status_code=404)

    payment.payment = True
    db.commit()
    return  RedirectResponse("/dashboard/tickets/event", status_code=303)

def _ticket_payload(event: Event, user, payment) -> str:
    """
    The exact string encoded in the QR code.
    Kept short and stable so the entrance scanner can parse it.
    """
    status = "paid" if payment.payment else "Unpaid"
    return (
        f"KARIBU|EVENT:{event.id}"
        f"|USER:{user.username}"
        f"|TITLE:{event.title}"
        f"|CARD:{payment.kind}"
        f"|PAYMENT STATUS:{status}"
    )

def _ticket_rows(db: Session, user):
    """All (Event, Event_payment) pairs for this user, newest first."""
    rows = db.execute(
        select(Event, Event_payment)
        .join(Event_payment, Event_payment.title == Event.title)
        .where(Event.creator_id==user.id)
        .order_by(Event.date_time.desc())
    ).all()
    return rows


@app.get("/dashboard/tickets/event", response_class=HTMLResponse)
def my_tickets(request: Request, db: DB):
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect
    tickets = []
    for event, payment in _ticket_rows(db, user):
        payload = _ticket_payload(event, user,payment)
        img = qrcode.make(payload)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        tickets.append({
            "event": event,
            "payment": payment,
            "qr_data_uri": f"data:image/png;base64,{b64}",
        })

    return render_dashboard(request, "joined_qr.html", user, "tickets", tickets=tickets)

def _ticket_rows_paid(db: Session, user):
    """Events this user joined AND has paid for."""
    return db.execute(
        select(Event, Event_payment)
        .join(Event_payment, Event_payment.title == Event.title)
        .where(
            Event_payment.user == user.username,
            Event_payment.payment.is_(True),
        )
        .order_by(Event.date_time.desc())
    ).all()


def _ticket_rows_unpaid(db: Session, user):
    """Events this user joined but has NOT paid for."""
    return db.execute(
        select(Event, Event_payment)
        .join(Event_payment, Event_payment.title == Event.title)
        .where(
            Event_payment.user == user.username,
            Event_payment.payment.is_(False),
        )
        .order_by(Event.date_time.desc())
    ).all()


@app.get("/dashboard/events/paid", response_class=HTMLResponse)
def paid_events_page(request: Request, db: DB):
        user, redirect = user_or_redirect(request, db)
        if redirect:
            return redirect
        tickets = []
        for event, payment in _ticket_rows_paid(db, user):
            payload = _ticket_payload(event, user, payment)
            img = qrcode.make(payload)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            tickets.append({
                "event": event,
                "payment": payment,
                "qr_data_uri": f"data:image/png;base64,{b64}",
            })
        return render_dashboard(request, "paid_event.html", user, "paid", tickets=tickets)


@app.get("/dashboard/events/unpaid", response_class=HTMLResponse)
def unpaid_events_page(request: Request, db: DB):
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect
    tickets = []
    for event, payment in _ticket_rows_unpaid(db, user):
        payload = _ticket_payload(event, user, payment)
        img = qrcode.make(payload)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        tickets.append({
            "event": event,
            "payment": payment,
            "qr_data_uri": f"data:image/png;base64,{b64}",
        })
    return render_dashboard(request, "unpaid_event.html", user, "unpaid", tickets=tickets)

@app.get("/dashboard/events/{event_id}/ticket.pdf")
def ticket_pdf(request: Request, db: DB, event_id: int):
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect

    # Confirm the user actually joined this event
    row = db.execute(
        select(Event, Event_payment)
        .join(Event_payment, Event_payment.title == Event.title)
        .where(Event.id == event_id, Event_payment.user == user.username)
    ).first()
    if not row:
        return render(request, "404.html", status_code=404)

    event, payment = row
    payload = _ticket_payload(event, user, payment)

    # Build QR image
    qr_img = qrcode.make(payload)
    qr_buf = io.BytesIO()
    qr_img.save(qr_buf, format="PNG")
    qr_buf.seek(0)

    # Build the PDF
    pdf_buf = io.BytesIO()
    c = canvas.Canvas(pdf_buf, pagesize=A4)
    width, height = A4

    # Header
    c.setFillColor(colors.HexColor("#14253a"))
    c.setFont("Helvetica-Bold", 20)
    c.drawString(25 * mm, height - 30 * mm, "Karibu Events Entry Ticket")

    c.setStrokeColor(colors.HexColor("#d3e3f6"))
    c.line(25 * mm, height - 33 * mm, width - 25 * mm, height - 33 * mm)

    # Event details (left column)
    y = height - 45 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(25 * mm, y, "Event")

    c.setFont("Helvetica", 11)
    c.setFillColor(colors.HexColor("#14253a"))
    c.drawString(25 * mm, y - 7 * mm, event.title)

    details = [
        ("Type", event.type),
        ("Date & time", event.date_time.strftime("%d %b %Y, %H:%M")),
        ("Venue", event.venue),
        ("Guest name", payment.full_name),
        ("Email", payment.email),
        ("Phone", payment.phone),
        ("Payment status", "Paid" if payment.payment else "Unpaid"),
    ]
    y -= 18 * mm
    for label, value in details:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(25 * mm, y, label)
        c.setFont("Helvetica", 10)
        c.drawString(60 * mm, y, str(value))
        y -= 7 * mm

    # QR image on the right
    qr_reader = ImageReader(qr_buf)
    qr_size = 80 * mm
    c.drawImage(
        qr_reader,
        width - 25 * mm - qr_size,
        height - 45 * mm - qr_size,
        width=qr_size,
        height=qr_size,
        preserveAspectRatio=True,
        mask="auto",
    )

    # Explanation block
    c.setFillColor(colors.HexColor("#5b6b7e"))
    c.setFont("Helvetica-Bold", 10)
    c.drawString(25 * mm, 60 * mm, "How to use this ticket")

    explanation = [
        "1. Bring this PDF (printed) or show the QR code on your phone at the entrance.",
        "2. A staff member will scan the QR code to check you in.",
        "3. Do not share this ticket — it is personal and tied to your account.",
        "4. If the payment status is Unpaid, please settle it before the event.",
        "5. The QR code contains your event ID and username, nothing else.",
    ]
    c.setFont("Helvetica", 9)
    ty = 52 * mm
    for line in explanation:
        c.drawString(25 * mm, ty, line)
        ty -= 5 * mm

    # Footer
    c.setFillColor(colors.HexColor("#5b6b7e"))
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(
        25 * mm,
        15 * mm,
        f"Ticket code: {payload}   ·   Generated for {user.full_name} (@{user.username})",
    )

    c.showPage()
    c.save()
    pdf_buf.seek(0)

    return StreamingResponse(
        pdf_buf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="ticket-{event.id}.pdf"'
        },
    )

# ----------------------------------------------------------------------- scan & report

def _parse_payload(payload: str) -> dict | None:
    """
    Parse 'KARIBU|EVENT:<id>|USER:<name>|TITLE:<title>|PAYMENT STATUS:<paid|Unpaid>'.
    Returns a dict or None if the string is malformed.
    """
    if not payload or not payload.startswith("KARIBU|"):
        return None
    parts = payload.split("|")
    data = {}
    for chunk in parts[1:]:
        if ":" not in chunk:
            continue
        key, value = chunk.split(":", 1)
        data[key.strip().upper()] = value.strip()
    if "EVENT" not in data or "USER" not in data:
        return None
    return data


def _event_owner_ids(db: Session, event_id: int) -> int | None:
    row = db.execute(select(Event.creator_id).where(Event.id == event_id)).first()
    return row[0] if row else None


@app.get("/dashboard/scan", response_class=HTMLResponse)
def scan_page(request: Request, db: DB):
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect
    events = db.scalars(
        select(Event).where(Event.creator_id == user.id).order_by(Event.date_time.desc())
    ).all()
    return render_dashboard(request, "scan.html", user, "scan", events=events, result=None)


@app.post("/dashboard/scan", response_class=HTMLResponse)
def scan_submit(
    request: Request,
    db: DB,
    payload: Annotated[str, Form()] = "",
):
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect

    events = db.scalars(
        select(Event).where(Event.creator_id == user.id).order_by(Event.date_time.desc())
    ).all()

    parsed = _parse_payload(payload)
    if not parsed:
        return render_dashboard(request, "scan.html", user, "scan", events=events,
                                result={"ok": False, "reason": "Unrecognised QR code."})

    try:
        event_id = int(parsed["EVENT"])
    except (KeyError, ValueError):
        return render_dashboard(request, "scan.html", user, "scan", events=events,
                                result={"ok": False, "reason": "Invalid event in QR code."})

    # Only the event's creator may scan for it
    if _event_owner_ids(db, event_id) != user.id:
        return render_dashboard(request, "scan.html", user, "scan", events=events,
                                result={"ok": False, "reason": "You cannot scan tickets for this event."})

    event = db.get(Event, event_id)
    payment = db.scalars(
        select(Event_payment).where(
            and_(Event_payment.title == event.title,
                 Event_payment.user == parsed["USER"])
        )
    ).first()

    if not payment:
        return render_dashboard(request, "scan.html", user, "scan", events=events,
                                result={"ok": False, "reason": "Guest not on this event's guest list."})

    # Already scanned?
    existing = db.scalars(
        select(Attendance).where(
            and_(Attendance.event_id == event_id,
                 Attendance.username == payment.user)
        )
    ).first()

    if existing:
        return render_dashboard(request, "scan.html", user, "scan", events=events,
                                result={
                                    "ok": True,
                                    "already": True,
                                    "guest": payment.full_name,
                                    "event": event.title,
                                    "paid": existing.paid,
                                    "when": existing.scanned_at,
                                })

    # Enforce payment before entry
    if not payment.payment:
        db.add(Attendance(
            event_id=event_id,
            username=payment.user,
            full_name=payment.full_name,
            email=payment.email,
            phone=payment.phone,
            attended=False,          # refused entry
            paid=False,
            scanned_by=user.username,
        ))
        db.commit()
        return render_dashboard(request, "scan.html", user, "scan", events=events,
                                result={
                                    "ok": False,
                                    "reason": "Payment not complete. Guest refused entry.",
                                    "guest": payment.full_name,
                                    "event": event.title,
                                })

    # All good → let them in
    db.add(Attendance(
        event_id=event_id,
        username=payment.user,
        full_name=payment.full_name,
        email=payment.email,
        phone=payment.phone,
        attended=True,
        paid=True,
        scanned_by=user.username,
    ))
    db.commit()
    return render_dashboard(request, "scan.html", user, "scan", events=events,
                            result={
                                "ok": True,
                                "guest": payment.full_name,
                                "event": event.title,
                                "paid": True,
                            })


@app.get("/dashboard/events/{event_id}/report", response_class=HTMLResponse)
def event_report(request: Request, db: DB, event_id: int):
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect

    event = db.get(Event, event_id)
    if not event or event.creator_id != user.id:
        return render(request, "404.html", status_code=404)

    # Everyone who is on the guest list for this event
    guests = db.scalars(
        select(Event_payment).where(Event_payment.title == event.title)
    ).all()

    # Scans that happened
    scans = {
        a.username: a
        for a in db.scalars(select(Attendance).where(Attendance.event_id == event_id)).all()
    }

    rows = []
    paid_count = attended_count = unpaid_count = absent_count = 0
    for g in guests:
        scan = scans.get(g.user)
        attended = bool(scan and scan.attended)
        paid = bool(g.payment)
        if paid:
            paid_count += 1
        else:
            unpaid_count += 1
        if attended:
            attended_count += 1
        else:
            absent_count += 1
        rows.append({
            "full_name": g.full_name,
            "email": g.email,
            "phone": g.phone,
            "paid": paid,
            "attended": attended,
            "scanned_at": scan.scanned_at if scan else None,
        })

    total = len(guests)
    def pct(n):
        return round((n / total) * 100, 1) if total else 0.0

    stats = {
        "total": total,
        "paid": paid_count,
        "unpaid": unpaid_count,
        "attended": attended_count,
        "absent": absent_count,
        "paid_pct": pct(paid_count),
        "unpaid_pct": pct(unpaid_count),
        "attended_pct": pct(attended_count),
        "absent_pct": pct(absent_count),
    }

    return render_dashboard(request, "event_report.html", user, "events",
                            event=event, rows=rows, stats=stats)

@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    return render(request, "forgot_password.html", values={}, errors={})


@app.post("/forgot-password", response_class=HTMLResponse)
def forgot_password_submit(
    request: Request,
    db: DB,
    full_name: Annotated[str, Form()] = "",
    username: Annotated[str, Form()] = "",
):
    full_name = full_name.strip()
    username = username.strip()

    errors = {}
    if not full_name:
        errors["full_name"] = "Enter your full name."
    if not username:
        errors["username"] = "Enter your username."

    if not errors:
        account = db.scalars(
            select(User).where(func.lower(User.username) == username.lower())
        ).first()

        if not account or account.full_name != full_name:
            errors["identity"] = "These details do not match an account."

    if errors:
        return render(request, "forgot_password.html", status_code=400,
                      values={"full_name": full_name, "username": username},
                      errors=errors)

    # Redirect to the reset form, carrying the (already verified) username
    return RedirectResponse(
        f"/forgot-password/reset?username={account.username}",
        status_code=303,
    )


@app.get("/forgot-password/reset", response_class=HTMLResponse)
def forgot_password_reset_page(request: Request, db: DB, username: str = ""):
    if not username:
        return RedirectResponse("/forgot-password", status_code=303)

    account = db.scalars(
        select(User).where(func.lower(User.username) == username.lower())
    ).first()
    if not account:
        return RedirectResponse("/forgot-password", status_code=303)

    return render(request, "reset_password_simple.html",
                  username=account.username, errors={})


@app.post("/forgot-password/reset", response_class=HTMLResponse)
def forgot_password_reset_submit(
    request: Request,
    db: DB,
    username: Annotated[str, Form()] = "",
    new_password: Annotated[str, Form()] = "",
    confirm_password: Annotated[str, Form()] = "",
):
    account = db.scalars(
        select(User).where(func.lower(User.username) == username.lower())
    ).first()
    if not account:
        return RedirectResponse("/forgot-password", status_code=303)

    errors = validate_password_change(
        {"current_password": "reset-flow",
         "new_password": new_password,
         "confirm_password": confirm_password},
        current_ok=True,
    )

    if errors:
        return render(request, "reset_password_simple.html", status_code=400,
                      username=account.username, errors=errors)

    account.password_hash = auth.hash_password(new_password)
    db.commit()

    return RedirectResponse("/login?reset=1", status_code=303)

@app.get("/dashboard/settings/account", response_class=HTMLResponse)
def settings_account_page(request: Request, db: DB, saved: int = 0):
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect
    values = {"full_name": user.full_name, "email": user.email, "phone": user.phone}
    return render_dashboard(request, "settings_account.html", user, "account",
                            values=values, errors={}, saved=bool(saved))

from validation import validate_password_change  # already imported

@app.get("/dashboard/events/keys", response_class=HTMLResponse)
def event_keys_page(request: Request, db: DB, saved_event: str = ""):
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect

    events = (
        db.query(Event)
        .filter(Event.creator_id == user.id)
        .order_by(Event.date_time.desc())
        .all()
    )

    return render_dashboard(
        request, "event_keys.html", user, "event_keys",
        events=events, saved_event=saved_event or None,
    )


@app.get("/dashboard/events/{event_id}/key", response_class=HTMLResponse)
def event_key_change_page(request: Request, db: DB, event_id: int):
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect

    event = db.get(Event, event_id)
    if not event or event.creator_id != user.id:
        return render(request, "404.html", status_code=404)

    return render_dashboard(
        request, "event_key_change.html", user, "event_keys",
        event=event, errors={},
    )


@app.post("/dashboard/events/{event_id}/key", response_class=HTMLResponse)
def event_key_change_save(
    request: Request,
    db: DB,
    event_id: int,
    current_key: Annotated[str, Form()] = "",
    new_key: Annotated[str, Form()] = "",
    confirm_key: Annotated[str, Form()] = "",
):
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect

    event = db.get(Event, event_id)
    if not event or event.creator_id != user.id:
        return render(request, "404.html", status_code=404)

    errors = {}

    # 1) Current key must match
    if not auth.verify_password(current_key, event.event_key):
        errors["current_key"] = "That is not the current event key."

    # 2) New key must pass the same rules as a password
    new_errors = validate_password_change(
        {
            "current_password": "verified",
            "new_password": new_key,
            "confirm_password": confirm_key,
        },
        current_ok=True,
    )
    if new_errors:
        errors.update(new_errors)

    if errors:
        return render_dashboard(
            request, "event_key_change.html", user, "event_keys",
            status_code=400, event=event, errors=errors,
        )

    event.event_key = auth.hash_password(new_key)
    db.commit()

    # Redirect to the list with a success banner; URL-encode the title
    from urllib.parse import quote
    return RedirectResponse(
        f"/dashboard/events/keys?saved_event={quote(event.title)}",
        status_code=303,
    )
@app.get("/dashboard/join/forgot-key", response_class=HTMLResponse)
def forgot_event_key_page(request: Request, db: DB):
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect
    return render_dashboard(
        request, "forgot_event_key.html", user, "join",
        values={}, errors={},
    )


@app.post("/dashboard/join/forgot-key", response_class=HTMLResponse)
def forgot_event_key_submit(
    request: Request,
    db: DB,
    title: Annotated[str, Form()] = "",
    creator_password: Annotated[str, Form()] = "",
):
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect

    title = title.strip()
    errors = {}

    if not title:
        errors["title"] = "Enter the event title."

    event = None
    if not errors:
        event = db.scalars(
            select(Event).where(func.lower(Event.title) == title.lower())
        ).first()
        if not event:
            errors["title"] = "No event matches that title."

    # Already joined?
    if not errors and event:
        already = db.scalars(
            select(Event_payment).where(
                and_(Event_payment.title == event.title,
                     Event_payment.user == user.username)
            )
        ).first()
        if already:
            errors["title"] = "You have already joined this event."

    # Verify with the creator's password
    if not errors and event:
        creator = db.get(User, event.creator_id)
        if not creator or not auth.verify_password(creator_password, creator.password_hash):
            errors["identity"] = "Organiser's password is incorrect."

    if errors:
        return render_dashboard(
            request, "forgot_event_key.html", user, "join",
            status_code=400,
            values={"title": title},
            errors=errors,
        )

    # Add the guest to the event
    db.add(Event_payment(
        title=event.title,
        user=user.username,
        full_name=user.full_name,
        email=user.email,
        phone=user.phone,
        kind="Single",
        payment=False,
    ))
    db.commit()

    return RedirectResponse("/dashboard/unpaid/event", status_code=303)

@app.post("/dashboard/settings/account", response_class=HTMLResponse)
def settings_account_save(
    request: Request,
    db: DB,
    full_name: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    phone: Annotated[str, Form()] = "",
):
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect
    cleaned, errors = validate_profile({"full_name": full_name, "email": email, "phone": phone})

    if not errors:
        messages = {"email": "Another account already uses this email.",
                    "phone": "Another account already uses this phone number."}
        taken = auth.taken_fields(db, cleaned["email"], cleaned["phone"], user.username, exclude=user)
        errors = {field: messages[field] for field in taken if field in messages}

    if not errors:
        try:
            user.full_name, user.email, user.phone = cleaned["full_name"], cleaned["email"], cleaned["phone"]
            db.commit()
            return RedirectResponse("/dashboard/settings/account?saved=1", status_code=303)
        except IntegrityError:
            db.rollback()
            errors = {"email": "These details were just taken by someone else."}

    db.rollback()  # throw away any half-edited values
    values = {"full_name": full_name, "email": email, "phone": phone}
    return render_dashboard(request, "settings_account.html", user, "account",
                            status_code=400, values=values, errors=errors, saved=False)


@app.get("/dashboard/settings/password", response_class=HTMLResponse)
def settings_password_page(request: Request, db: DB, changed: int = 0):
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect
    return render_dashboard(request, "settings_password.html", user, "password",
                            errors={}, changed=bool(changed))


@app.post("/dashboard/settings/password", response_class=HTMLResponse)
def settings_password_save(
    request: Request,
    db: DB,
    current_password: Annotated[str, Form()] = "",
    new_password: Annotated[str, Form()] = "",
    confirm_password: Annotated[str, Form()] = "",
):
    user, redirect = user_or_redirect(request, db)
    if redirect:
        return redirect
    current_ok = auth.verify_password(current_password, user.password_hash)
    errors = validate_password_change(
        {"current_password": current_password, "new_password": new_password,
         "confirm_password": confirm_password}, current_ok)

    if errors:
        return render_dashboard(request, "settings_password.html", user, "password",
                                status_code=400, errors=errors, changed=False)

    remember = auth.was_remembered(request)
    user.password_hash = auth.hash_password(new_password)
    db.commit()
    # Changing the password ends old sessions, so give this browser a fresh one.
    response = RedirectResponse("/dashboard/settings/password?changed=1", status_code=303)
    set_session(response, "user", user, remember)
    return response