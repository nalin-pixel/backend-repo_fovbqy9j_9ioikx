"""
UzbCinemaHub Schemas

Define MongoDB collection schemas using Pydantic models.
Each model name lowercased is used as collection name.
"""
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional
from datetime import datetime

# ---------- User & Auth ----------
class User(BaseModel):
    name: str = Field(..., description="Full name")
    email: EmailStr = Field(..., description="Email address")
    password_hash: str = Field(..., description="Hashed password")
    role: str = Field("user", description="Role: user|moderator|admin")
    avatar_url: Optional[str] = Field(None)
    is_active: bool = Field(True)
    email_verified: bool = Field(False)

# ---------- Movies & Series ----------
class MovieSource(BaseModel):
    label: str = Field(..., description="Quality label e.g., 1080p")
    url: str = Field(..., description="Streaming URL or file path")

class Subtitle(BaseModel):
    lang: str = Field(..., description="Language code e.g., uz, ru, en")
    url: str = Field(...)

class Movie(BaseModel):
    title: str = Field(...)
    original_title: Optional[str] = Field(None)
    description: Optional[str] = Field(None)
    year: int = Field(...)
    duration_min: int = Field(..., ge=1)
    genres: List[str] = Field(default_factory=list)
    director: Optional[str] = None
    cast: List[str] = Field(default_factory=list)
    country: Optional[str] = None
    poster_url: Optional[str] = None
    trailer_youtube: Optional[str] = None
    sources: List[MovieSource] = Field(default_factory=list)
    subtitles: List[Subtitle] = Field(default_factory=list)
    audio_tracks: List[str] = Field(default_factory=list)
    status: str = Field("active", description="active|inactive")
    imdb_rating: Optional[float] = Field(None, ge=0, le=10)
    avg_rating: float = Field(0)
    views: int = Field(0)
    tags: List[str] = Field(default_factory=list)

class Rating(BaseModel):
    user_id: str
    movie_id: str
    value: float = Field(..., ge=0, le=10)
    created_at: Optional[datetime] = None

class Comment(BaseModel):
    user_id: str
    movie_id: str
    text: str
    spoiler: bool = False
    status: str = Field("pending", description="pending|approved|rejected")
    created_at: Optional[datetime] = None

class Watchlist(BaseModel):
    user_id: str
    movie_id: str
    created_at: Optional[datetime] = None

class ViewHistory(BaseModel):
    user_id: str
    movie_id: str
    position_sec: int = 0
    updated_at: Optional[datetime] = None
