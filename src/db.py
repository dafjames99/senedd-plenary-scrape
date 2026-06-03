import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import relationship
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")
engine = create_engine(DB_URL)
SessionLocal = sessionmaker(autocommit = False, autoflush = False, bind = engine)
Base = declarative_base()

class Member(Base):
	__tablename__ = "members"

	member_id = Column(Integer, primary_key=True, index=True)
	name_english = Column(String(255), nullable=False)
	current_party = Column(String(100))

	contributions = relationship("Contribution", back_populates="member")


class Meeting(Base):
	__tablename__ = "meetings"

	meeting_id = Column(Integer, primary_key=True, index=True)
	meeting_date = Column(DateTime, nullable=False)
	meeting_type = Column(String(50), nullable=False)

	contributions = relationship("Contribution", back_populates="meeting")


class Contribution(Base):
	__tablename__ = "contributions"

	contribution_id = Column(Integer, primary_key=True, autoincrement=True)
	meeting_id = Column(Integer, ForeignKey("meetings.meeting_id"))
	member_id = Column(Integer, ForeignKey("members.member_id"))
	order_index = Column(Integer, nullable=False)
	agenda_item = Column(String(500))
	text_verbatim = Column(Text, nullable=False)
	text_translated = Column(Text)
	text_clean_english = Column(Text)

	meeting = relationship("Meeting", back_populates="contributions")
	member = relationship("Member", back_populates="contributions")
 
 