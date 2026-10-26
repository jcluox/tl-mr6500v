from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Sms:
    index: int
    sender: str
    content: str
    received_at: str
    unread: bool

    @property
    def uid(self) -> str:
        # The router's index can restart from 1 after the inbox is cleared, so the time and sender are part of the id
        return f"{self.index}|{self.received_at}|{self.sender}"

    def as_dict(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        return f"📩 New SMS\nFrom: {self.sender}\nTime: {self.received_at}\n\n{self.content}"
