"""A small, local-first project board."""

from .db import Board, BoardError, AuthenticationError, NotFound, ValidationError

__all__ = ["Board", "BoardError", "AuthenticationError", "NotFound", "ValidationError"]
__version__ = "0.1.0"

