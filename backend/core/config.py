import os
import json 
from dotenv import load_dotenv
load_dotenv()
from utils.secrets_manager import get_secret


class Settings:
    def __init__(self):
        db_creds = get_secret("db-creds", "ap-south-1")
        db_creds = json.loads(db_creds)
        import pprint
        pprint.pprint(db_creds)
        self.DATABASE_NAME = db_creds["DB_NAME"] 
        self.DATABASE_USER = db_creds["DB_USER"]
        self.DATABASE_PASSWORD = db_creds["DB_PASS"]
        self.DATABASE_HOST = db_creds["DB_HOST"]
        #self.DATABASE_HOST = 'database-1.cd2q86w4mjdh.ap-south-1.rds.amazonaws.com'
        # self.DATABASE_HOST = 'localhost'
        self.DATABASE_PORT = db_creds["DB_PORT"]
        self.DATABASE_URL = f"postgresql://{self.DATABASE_USER}:{self.DATABASE_PASSWORD}@{self.DATABASE_HOST}:{self.DATABASE_PORT}/{self.DATABASE_NAME}"
        if not self.DATABASE_URL:
            raise RuntimeError("DATABASE_URL not configured")
        
        OPENAI_API_KEY = get_secret("open-api-key", "ap-south-1")
        OPENAI_API_KEY = json.loads(OPENAI_API_KEY)
        self.OPENAI_API_KEY = OPENAI_API_KEY["openai-api-key"]

settings = Settings()
