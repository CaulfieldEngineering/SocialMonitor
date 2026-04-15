"""Qt signal bridge for async -> UI communication."""

from PyQt6.QtCore import QObject, pyqtSignal

from social_monitor.models import ScoredPost


class JsonScoredPost:
    """Serializable snapshot of a ScoredPost for passing across threads."""

    def __init__(self, sp: ScoredPost, trigger_info: str):
        self.source = sp.post.source
        self.source_name = sp.post.metadata.get("source_name", sp.post.source.replace("_", " ").title())
        self.post_id = sp.post.post_id
        self.title = sp.post.title
        self.body = sp.post.body
        self.author = sp.post.author
        self.url = sp.post.url
        self.timestamp = sp.post.timestamp
        self.score = sp.score
        self.explanation = sp.explanation
        self.trigger_info = trigger_info
        self.metadata = dict(sp.post.metadata)


class SignalBridge(QObject):
    """Bridges async poller events to Qt UI updates."""

    # Emitted for every scored post (whether notified or not)
    post_scored = pyqtSignal(object)  # JsonScoredPost

    # Emitted when AI scoring starts/finishes a batch
    ai_status = pyqtSignal(str)  # status message like "Scoring 5 posts..." or "Idle"

    # Emitted when a source poll completes
    source_status = pyqtSignal(str, int)  # source_name, new_post_count
