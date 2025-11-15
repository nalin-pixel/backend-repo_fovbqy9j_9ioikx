import os
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from bson.objectid import ObjectId
from datetime import datetime, timezone, timedelta
from passlib.context import CryptContext
import jwt

from database import db, create_document, get_documents
from schemas import Movie, Rating, Comment, Watchlist, ViewHistory, User

app = FastAPI(title="UzbCinemaHub API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------- Auth Setup ---------
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret")
JWT_ALG = "HS256"
ACCESS_TTL_MIN = 60 * 24 * 7  # 7 days

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def oid(s: str) -> ObjectId:
    try:
        return ObjectId(s)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")


def serialize(doc):
    if not doc:
        return doc
    doc["id"] = str(doc.pop("_id"))
    for k, v in list(doc.items()):
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
    return doc


# --------- Basic ---------
@app.get("/")
def root():
    return {"name": "UzbCinemaHub API", "status": "ok"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set",
        "database_name": "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set",
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["connection_status"] = "Connected"
            response["collections"] = db.list_collection_names()
    except Exception as e:
        response["database"] = f"⚠️ {str(e)[:80]}"
    return response


# --------- Auth Models ---------
class RegisterPayload(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginPayload(BaseModel):
    email: EmailStr
    password: str


class AuthUser(BaseModel):
    id: str
    name: str
    email: EmailStr
    role: str
    avatar_url: Optional[str] = None


# --------- Auth Helpers ---------

def create_tokens(user: dict):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user["_id"]),
        "name": user.get("name"),
        "email": user.get("email"),
        "role": user.get("role", "user"),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ACCESS_TTL_MIN)).timestamp()),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)
    return token


def get_user_from_token(authorization: Optional[str] = Header(None)) -> Optional[dict]:
    if not authorization:
        return None
    try:
        scheme, token = authorization.split(" ", 1)
        if scheme.lower() != "bearer":
            return None
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        uid = payload.get("sub")
        if not uid:
            return None
        user = db["user"].find_one({"_id": oid(uid)})
        return user
    except Exception:
        return None


# --------- Auth Routes ---------
@app.post("/api/auth/register")
def register(payload: RegisterPayload):
    if db["user"].find_one({"email": payload.email}):
        raise HTTPException(400, "Email already registered")
    hashed = pwd_ctx.hash(payload.password)
    data = {
        "name": payload.name,
        "email": payload.email,
        "password_hash": hashed,
        "role": "user",
        "avatar_url": None,
        "is_active": True,
        "email_verified": False,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    inserted = db["user"].insert_one(data).inserted_id
    user = db["user"].find_one({"_id": inserted})
    token = create_tokens(user)
    u = serialize(user)
    u.pop("password_hash", None)
    return {"token": token, "user": u}


@app.post("/api/auth/login")
def login(payload: LoginPayload):
    user = db["user"].find_one({"email": payload.email})
    if not user or not pwd_ctx.verify(payload.password, user.get("password_hash", "")):
        raise HTTPException(401, "Invalid credentials")
    if not user.get("is_active", True):
        raise HTTPException(403, "User inactive")
    token = create_tokens(user)
    u = serialize(user)
    u.pop("password_hash", None)
    return {"token": token, "user": u}


@app.get("/api/auth/me")
def me(current_user: Optional[dict] = Depends(get_user_from_token)):
    if not current_user:
        raise HTTPException(401, "Not authenticated")
    u = serialize(current_user)
    u.pop("password_hash", None)
    return u


# --------- Movies ---------
class MovieCreate(Movie):
    pass


@app.post("/api/movies")
def create_movie(movie: MovieCreate, current_user: Optional[dict] = Depends(get_user_from_token)):
    # optional simple protection: only admin can create
    if current_user and current_user.get("role") != "admin":
        raise HTTPException(403, "Forbidden")
    movie_dict = movie.model_dump()
    movie_dict["created_at"] = datetime.now(timezone.utc)
    movie_dict["updated_at"] = datetime.now(timezone.utc)
    inserted_id = db["movie"].insert_one(movie_dict).inserted_id
    doc = db["movie"].find_one({"_id": inserted_id})
    return serialize(doc)


@app.get("/api/movies")
def list_movies(
    q: Optional[str] = Query(None, description="Search by title, cast, director"),
    genre: Optional[str] = None,
    sort: Optional[str] = Query("new", description="new|popular|rating"),
    limit: int = 24,
):
    filter_q = {}
    if q:
        filter_q["$or"] = [
            {"title": {"$regex": q, "$options": "i"}},
            {"original_title": {"$regex": q, "$options": "i"}},
            {"cast": {"$elemMatch": {"$regex": q, "$options": "i"}}},
            {"director": {"$regex": q, "$options": "i"}},
        ]
    if genre:
        filter_q["genres"] = genre

    cursor = db["movie"].find(filter_q)
    if sort == "popular":
        cursor = cursor.sort("views", -1)
    elif sort == "rating":
        cursor = cursor.sort("avg_rating", -1)
    else:
        cursor = cursor.sort("created_at", -1)

    cursor = cursor.limit(max(1, min(limit, 100)))
    return [serialize(d) for d in cursor]


@app.get("/api/movies/{movie_id}")
def get_movie(movie_id: str):
    doc = db["movie"].find_one({"_id": oid(movie_id)})
    if not doc:
        raise HTTPException(404, "Movie not found")
    return serialize(doc)


@app.get("/api/movies/{movie_id}/similar")
def similar_movies(movie_id: str, limit: int = 8):
    doc = db["movie"].find_one({"_id": oid(movie_id)})
    if not doc:
        raise HTTPException(404, "Movie not found")
    genres = doc.get("genres", [])
    cursor = db["movie"].find({"_id": {"$ne": doc["_id"]}, "genres": {"$in": genres}}).limit(limit)
    return [serialize(d) for d in cursor]


# --------- Ratings ---------
class RatingCreate(BaseModel):
    user_id: str
    value: float


@app.post("/api/movies/{movie_id}/ratings")
def rate_movie(movie_id: str, payload: RatingCreate):
    db["rating"].update_one(
        {"movie_id": movie_id, "user_id": payload.user_id},
        {"$set": {"value": payload.value, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    cur = db["rating"].find({"movie_id": movie_id})
    vals = [r.get("value", 0) for r in cur]
    avg = sum(vals) / len(vals) if vals else 0
    db["movie"].update_one({"_id": oid(movie_id)}, {"$set": {"avg_rating": avg}})
    return {"ok": True, "avg_rating": avg}


@app.get("/api/movies/{movie_id}/ratings")
def get_ratings(movie_id: str):
    cur = db["rating"].find({"movie_id": movie_id}).limit(100)
    res = []
    for r in cur:
        r["id"] = str(r.pop("_id"))
        res.append(r)
    return res


# --------- Comments ---------
class CommentCreate(BaseModel):
    user_id: str
    text: str
    spoiler: bool = False


@app.post("/api/movies/{movie_id}/comments")
def add_comment(movie_id: str, payload: CommentCreate):
    data = {
        "movie_id": movie_id,
        "user_id": payload.user_id,
        "text": payload.text,
        "spoiler": payload.spoiler,
        "status": "approved",
        "created_at": datetime.now(timezone.utc),
    }
    inserted = db["comment"].insert_one(data).inserted_id
    doc = db["comment"].find_one({"_id": inserted})
    doc["id"] = str(doc.pop("_id"))
    return doc


@app.get("/api/movies/{movie_id}/comments")
def list_comments(movie_id: str):
    cur = db["comment"].find({"movie_id": movie_id, "status": "approved"}).sort("created_at", -1).limit(100)
    res = []
    for c in cur:
        c["id"] = str(c.pop("_id"))
        res.append(c)
    return res


# --------- Watchlist ---------
class WatchlistToggle(BaseModel):
    user_id: str


@app.post("/api/movies/{movie_id}/watchlist")
def toggle_watchlist(movie_id: str, payload: WatchlistToggle):
    existing = db["watchlist"].find_one({"movie_id": movie_id, "user_id": payload.user_id})
    if existing:
        db["watchlist"].delete_one({"_id": existing["_id"]})
        return {"saved": False}
    db["watchlist"].insert_one({
        "movie_id": movie_id,
        "user_id": payload.user_id,
        "created_at": datetime.now(timezone.utc),
    })
    return {"saved": True}


@app.get("/api/users/{user_id}/watchlist")
def get_watchlist(user_id: str):
    cur = db["watchlist"].find({"user_id": user_id}).sort("created_at", -1).limit(200)
    movie_ids = [w.get("movie_id") for w in cur]
    movies = db["movie"].find({"_id": {"$in": [ObjectId(m) for m in movie_ids if ObjectId.is_valid(m)]}})
    return [serialize(m) for m in movies]


# --------- Continue watching ---------
class ProgressUpdate(BaseModel):
    user_id: str
    position_sec: int


@app.post("/api/movies/{movie_id}/progress")
def update_progress(movie_id: str, payload: ProgressUpdate):
    db["viewhistory"].update_one(
        {"movie_id": movie_id, "user_id": payload.user_id},
        {
            "$set": {
                "position_sec": payload.position_sec,
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )
    return {"ok": True}


@app.get("/api/users/{user_id}/continue")
def get_continue_watching(user_id: str, limit: int = 10):
    cur = db["viewhistory"].find({"user_id": user_id}).sort("updated_at", -1).limit(limit)
    items = []
    for vh in cur:
        movie = db["movie"].find_one({"_id": ObjectId(vh["movie_id"])}) if ObjectId.is_valid(vh.get("movie_id", "")) else None
        if movie:
            m = serialize(movie)
            m["position_sec"] = vh.get("position_sec", 0)
            items.append(m)
    return items


# --------- Demo seed ---------
@app.post("/api/seed")
def seed_demo():
    if db["movie"].count_documents({}) > 0:
        return {"ok": True, "message": "Movies already seeded"}
    demo_movies = [
        {
            "title": "Qahramon",
            "original_title": "Hero",
            "description": "Action sahnalariga boy O'zbekona jangari film.",
            "year": 2024,
            "duration_min": 118,
            "genres": ["Action"],
            "director": "A. Karimov",
            "cast": ["J. Aliyev", "M. Yuldasheva"],
            "country": "UZ",
            "poster_url": "https://images.unsplash.com/photo-1524985069026-dd778a71c7b4?q=80&w=800&auto=format&fit=crop",
            "trailer_youtube": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "sources": [{"label": "1080p", "url": "https://samplelib.com/lib/preview/mp4/sample-5s.mp4"}],
            "subtitles": [],
            "audio_tracks": ["original"],
            "status": "active",
            "imdb_rating": 6.8,
            "avg_rating": 7.5,
            "views": 1250,
            "tags": ["jangari"],
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
        {
            "title": "Qalb Sadosi",
            "original_title": "Echo of Heart",
            "description": "Dramatik voqealar va chuqur his-tuyg'ular haqida film.",
            "year": 2023,
            "duration_min": 102,
            "genres": ["Drama"],
            "director": "D. Xolmatova",
            "cast": ["S. Abdullayev", "N. To'xtasinova"],
            "country": "UZ",
            "poster_url": "https://images.unsplash.com/photo-1497032628192-86f99bcd76bc?q=80&w=800&auto=format&fit=crop",
            "trailer_youtube": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "sources": [{"label": "720p", "url": "https://samplelib.com/lib/preview/mp4/sample-5s.mp4"}],
            "subtitles": [],
            "audio_tracks": ["original"],
            "status": "active",
            "imdb_rating": 7.2,
            "avg_rating": 8.1,
            "views": 2200,
            "tags": ["drama"],
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
        {
            "title": "Kulgu Kechasi",
            "original_title": "Night of Laughter",
            "description": "Qiziqarli komediya, oilaviy tomosha uchun mos.",
            "year": 2022,
            "duration_min": 95,
            "genres": ["Comedy"],
            "director": "I. Qodirov",
            "cast": ["B. Rasulov", "D. Abduvaliyeva"],
            "country": "UZ",
            "poster_url": "https://images.unsplash.com/photo-1542204165-65bf26472b9b?q=80&w=800&auto=format&fit=crop",
            "trailer_youtube": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "sources": [{"label": "480p", "url": "https://samplelib.com/lib/preview/mp4/sample-5s.mp4"}],
            "subtitles": [],
            "audio_tracks": ["original"],
            "status": "active",
            "imdb_rating": 6.0,
            "avg_rating": 7.0,
            "views": 3400,
            "tags": ["komediya"],
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    ]
    db["movie"].insert_many(demo_movies)
    return {"ok": True, "count": len(demo_movies)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
