"""Módulo de persistencia de datos (Supabase en la nube / SQLite local)."""
from src.database.db import DatabaseManager
from src.database.supabase_client import SupabaseManager

__all__ = ["DatabaseManager", "SupabaseManager"]
