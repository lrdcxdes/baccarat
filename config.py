import os

from dotenv import load_dotenv

load_dotenv()

FONTAWESOME_URL = os.getenv("FONTAWESOME_URL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
