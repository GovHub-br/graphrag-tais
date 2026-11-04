"""Resultado de cada resposta, guardado para os cliques nos botões.

Por que guardar em vez de recalcular: a trilha "Como esse dado foi calculado?"
tem de mostrar o SQL que gerou O número que a pessoa acabou de ler. Rodar o
motor de novo para a mesma pergunta custa outra chamada de LLM e, pior, pode
gerar um SQL diferente — e aí o número da trilha não bate com o da resposta.

O que é guardado: a pergunta, o `answer` do motor (SQL, explicação,
referências) e o resultado executado (linhas já mascaradas pelo
`McpSqlExecutor`, ADR-0018). Vai para o Redis do stack, com validade
(`JOURNEY_STATE_TTL`); sem Redis, cai num dicionário em memória do processo.
Se o estado sumiu (validade, restart), a trilha recalcula e diz isso.
"""
from __future__ import annotations

import json
import secrets
from collections import OrderedDict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from loguru import logger

_PREFIXO = "tais:jornada:"
_MEMORIA_MAX = 256


def new_ref() -> str:
    return secrets.token_hex(6)


def _default(value: Any) -> Any:
    if isinstance(value, Decimal):
        # Inteiro continua inteiro: "12345" de código não pode virar 12345.0.
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


class JourneyStore:
    def __init__(self, ttl: int | None = None, client: Any = None) -> None:
        from rag.settings.configs import settings

        self.ttl = ttl or settings.JOURNEY_STATE_TTL
        self._client = client
        self._client_failed = False
        self._memory: "OrderedDict[str, str]" = OrderedDict()

    # ------------------------------------------------------------------ #
    def _redis(self):
        if self._client is not None or self._client_failed:
            return self._client
        try:
            import redis
            from rag.settings.configs import settings

            client = redis.Redis(
                host=settings.REDIS_HOST, port=settings.REDIS_PORT,
                password=settings.REDIS_PASSWORD or None, db=settings.REDIS_DB,
                socket_timeout=2, socket_connect_timeout=2,
            )
            client.ping()
            self._client = client
        except Exception as exc:  # noqa: BLE001 — sem Redis, memória do processo
            logger.warning("[jornada/estado] Redis indisponível ({}); usando memória", exc)
            self._client_failed = True
        return self._client

    # ------------------------------------------------------------------ #
    def save(self, ref: str, payload: Dict[str, Any]) -> None:
        texto = json.dumps(payload, ensure_ascii=False, default=_default)
        client = self._redis()
        if client is not None:
            try:
                client.set(_PREFIXO + ref, texto, ex=self.ttl)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("[jornada/estado] falha ao gravar no Redis ({})", exc)
        self._memory[ref] = texto
        self._memory.move_to_end(ref)
        while len(self._memory) > _MEMORIA_MAX:
            self._memory.popitem(last=False)

    def load(self, ref: str) -> Optional[Dict[str, Any]]:
        if not ref:
            return None
        texto = None
        client = self._redis()
        if client is not None:
            try:
                bruto = client.get(_PREFIXO + ref)
                texto = bruto.decode("utf-8") if isinstance(bruto, bytes) else bruto
            except Exception as exc:  # noqa: BLE001
                logger.warning("[jornada/estado] falha ao ler do Redis ({})", exc)
        if texto is None:
            texto = self._memory.get(ref)
        return json.loads(texto) if texto else None
