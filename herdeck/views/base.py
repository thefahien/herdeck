from abc import ABC, abstractmethod
from typing import Optional


class BaseView(ABC):
    name: str = "Dashboard"
    key_hint: str = "1"

    @abstractmethod
    def update(self, force: bool = False) -> None:
        """Fetch latest data from backend."""
        pass

    @abstractmethod
    def draw(self, stdscr, start_y: int, max_y: int, max_x: int) -> int:
        """Render the dashboard content. Returns the next available row (y)."""
        pass

    @abstractmethod
    def handle_key(self, ch: int) -> bool:
        """Handle view-specific key press. Return True if handled, False otherwise."""
        pass

    def handle_mouse_click(self, mx: int, my: int) -> bool:
        """Handle mouse click inside view content. Return True if handled."""
        return False

    def get_status(self) -> str:
        """Return a short status string for the footer."""
        return "OK"

    def print_once(self) -> None:
        """Print one-shot status snapshot to stdout."""
        pass
