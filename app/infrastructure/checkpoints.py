"""Checkpoints locais do grafo, reconciliados com os turnos públicos do MongoDB."""

import hashlib
import json
import logging
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Overwrite


COMPLETE_TURN_NODE = "concluir_turno"
logger = logging.getLogger(__name__)


def checkpoint_messages(history: list[dict[str, str]], offset: int = 0):
    """IDs por posição evitam duplicações; o Mongo define o conteúdo autorizado."""
    return [
        (HumanMessage if item["role"] in {"human", "user"} else AIMessage)(
            content=item["content"], id=f"mongo-{offset + index}",
        )
        for index, item in enumerate(history)
    ]


@dataclass
class CheckpointTurn:
    memory: "ConversationCheckpoints"
    graph: object
    session_id: str
    config: dict
    committed: bool = False

    async def commit(self, history: list[dict[str, str]], offset: int):
        """Após gravar no Mongo, retém só um snapshot público, sem decisões/URLs."""
        thread_id = self.config["configurable"]["thread_id"]
        try:
            await self.memory.saver.adelete_thread(thread_id)
            await self.graph.aupdate_state(
                self.config,
                {"session_id": self.session_id, "messages": Overwrite(checkpoint_messages(history, offset))},
                as_node=COMPLETE_TURN_NODE,
            )
        except Exception as error:
            # O Mongo já confirmou o turno. Uma falha da memória local não pode
            # transformá-lo em erro nem causar repetição de uma ação pelo cliente.
            logger.warning("Falha ao compactar checkpoint tipo=%s", type(error).__name__)
        else:
            self.committed = True


class ConversationCheckpoints:
    """MemorySaver com limite de sessões e expiração ociosa, sem banco adicional.

    O grafo salva checkpoints durante a execução, incluindo os subgrafos. No fim,
    um snapshot público substitui os estados transitórios. Não há replay de tools.
    """

    def __init__(self, *, max_threads: int = 128, idle_seconds: float = 1800):
        if max_threads < 20 or idle_seconds <= 0:
            raise ValueError("Memória exige pelo menos 20 threads e expiração positiva.")
        self.saver = InMemorySaver()
        self._clock = time.monotonic
        self.max_threads = max_threads
        self.idle_seconds = idle_seconds
        self._threads: OrderedDict[str, float] = OrderedDict()
        self._active: set[str] = set()

    @staticmethod
    def config(uid: str, session_id: str):
        identity = json.dumps([uid, session_id], ensure_ascii=False).encode("utf-8")
        return {"configurable": {"thread_id": hashlib.sha256(identity).hexdigest()}, "recursion_limit": 20}

    async def discard(self, uid: str, session_id: str):
        await self._discard_thread(self.config(uid, session_id)["configurable"]["thread_id"])

    async def _discard_thread(self, thread_id: str):
        await self.saver.adelete_thread(thread_id)
        self._threads.pop(thread_id, None)

    async def _prune(self):
        now = self._clock()
        for thread_id, touched in list(self._threads.items()):
            if thread_id not in self._active and (
                now - touched >= self.idle_seconds or len(self._threads) > self.max_threads
            ):
                await self._discard_thread(thread_id)

    @asynccontextmanager
    async def turn(self, graph, uid: str, session_id: str):
        config = self.config(uid, session_id)
        thread_id = config["configurable"]["thread_id"]
        self._active.add(thread_id)
        self._threads[thread_id] = self._clock()
        self._threads.move_to_end(thread_id)
        try:
            await self._prune()
            turn = CheckpointTurn(self, graph, session_id, config)
            try:
                yield turn
            finally:
                if not turn.committed:
                    # Bloqueio, timeout e falha de persistência não viram memória.
                    await self._discard_thread(thread_id)
                else:
                    self._threads[thread_id] = self._clock()
                    self._threads.move_to_end(thread_id)
        finally:
            self._active.discard(thread_id)

    def clear(self):
        for thread_id in list(self._threads):
            self.saver.delete_thread(thread_id)
        self._threads.clear()
