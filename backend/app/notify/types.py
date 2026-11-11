from dataclasses import dataclass, field
from typing import Literal

Level = Literal["info", "success", "warning", "error"]


@dataclass
class Notification:
    """A message to send. Plain data, so it can be passed to a Temporal activity."""

    title: str
    body: str = ""
    level: Level = "info"
    # Short label and value pairs, shown as a grid. Values are ids, statuses, counts.
    fields: dict[str, str] = field(default_factory=dict)
    # Optional link, such as the workflow in the Temporal UI.
    link: str | None = None
    link_text: str = "Open"
