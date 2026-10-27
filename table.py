'''
import sqlite3

con = sqlite3.connect("karibu.db")
rows = con.execute("select sql from sqlite_master where name = 'events_payments'").fetchall()
table = con.execute("select name from sqlite_master where type='table'").fetchall()

print(rows)
'''
from models import SessionLocal, Event_payment,User,Event
from sqlalchemy import select

with SessionLocal() as db:
    events = db.scalars(select(Event).where(Event.creator_id == 2)).all()
    print(events==None)