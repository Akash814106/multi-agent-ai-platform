from pymongo import MongoClient
from backend.utils.env_loader import *
import os



mongo_uri = os.getenv("MONGO_URI")

if not mongo_uri:
    raise ValueError("MONGO_URI environment variable is not set.")

# Return database failures within the frontend account request timeout.
client = MongoClient(mongo_uri, timeoutMS=6000)

db = client["multi_agent_ai"]

users_collection = db["users"]

conversations_collection = db["conversations"]
