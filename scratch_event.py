from models import Event, User, SessionLocal
from sqlalchemy import select
from datetime import datetime

with SessionLocal() as db:
    event=db.scalars(select(Event)).all()
    for event in event:

        print(event.id,event.title,event.venue,event.date_time)
