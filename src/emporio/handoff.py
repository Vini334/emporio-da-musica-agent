"""Hand-off queue to the human team (SPEC §11).

Each hand-off is one JSON line in ``storage/handoffs.jsonl``. The protocol is
``EMP-AAAAMMDD-NNNN``: the store-local date plus a per-day sequence. This is the only write the
agent ever makes (SPEC §3).
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, get_args

from emporio import clock, config

HandoffReason = Literal[
    "reclamacao", "pedido_atrasado", "cotacao_frete", "garantia", "cancelamento", "lgpd", "outro"
]
REASONS: tuple[str, ...] = get_args(HandoffReason)
SLA = "retorno em até 24h úteis"

_lock = threading.Lock()


@dataclass(frozen=True)
class Handoff:
    protocol: str
    created_at: str
    reason: str
    summary: str
    contact: str | None
    session_id: str | None


def _protocol_prefix(moment: datetime) -> str:
    return f"EMP-{moment:%Y%m%d}-"


def next_protocol(queue: Path, moment: datetime) -> str:
    """Next protocol for ``moment``'s day, continuing the sequence already in ``queue``."""
    prefix = _protocol_prefix(moment)
    last = 0
    if queue.exists():
        for line in queue.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            protocol = json.loads(line).get("protocol", "")
            if protocol.startswith(prefix):
                last = max(last, int(protocol.removeprefix(prefix)))
    return f"{prefix}{last + 1:04d}"


def create_handoff(
    reason: str,
    summary: str,
    contact: str | None = None,
    session_id: str | None = None,
    queue: Path | None = None,
    at: datetime | None = None,
) -> Handoff:
    """Append a hand-off to the queue and return it with its protocol."""
    if reason not in REASONS:
        reason = "outro"
    queue = queue or config.HANDOFFS_FILE
    moment = at or clock.now()
    queue.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        handoff = Handoff(
            protocol=next_protocol(queue, moment),
            created_at=moment.isoformat(timespec="seconds"),
            reason=reason,
            summary=summary.strip(),
            contact=(contact or "").strip() or None,
            session_id=session_id,
        )
        with queue.open("a", encoding="utf-8") as file:
            file.write(json.dumps(asdict(handoff), ensure_ascii=False) + "\n")
    return handoff
