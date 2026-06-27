
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database.base import Base
from core.config import settings
engine = create_engine(settings.DATABASE_URL , echo=True )
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()