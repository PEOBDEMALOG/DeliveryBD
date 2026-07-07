# peo_bd/core/historico.py
# Serviço central de registro de histórico de eventos do sistema.
# Instancie com a sessão do banco disponível em cada agente.

import asyncio
import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.models import HistoricoEventos

logger = logging.getLogger(__name__)


class HistoricoService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def registrar(
        self,
        tipo_evento: str,
        origem: str,
        ator_tipo: str,
        descricao: str,
        resultado: str,
        ator_nome: str | None = None,
        remessa_id: int | None = None,
        transportadora_id: int | None = None,
        cd_id: int | None = None,
        gravidade: str | None = None,
        visibilidade: str = "interno",
        dados_extra: dict | None = None,
    ) -> HistoricoEventos:
        evento = HistoricoEventos(
            timestamp=datetime.utcnow(),
            tipo_evento=tipo_evento,
            origem=origem,
            ator_tipo=ator_tipo,
            ator_nome=ator_nome,
            remessa_id=remessa_id,
            transportadora_id=transportadora_id,
            cd_id=cd_id,
            descricao=descricao,
            resultado=resultado,
            gravidade=gravidade,
            visibilidade=visibilidade,
            dados_extra=dados_extra,
        )
        self.db.add(evento)
        await self.db.flush()

        # Dispara e-mail de alerta para erros graves — não bloqueia o fluxo principal.
        if tipo_evento == "erro_sistema" and gravidade in ("alerta", "critico"):
            asyncio.ensure_future(self._enviar_alerta_email(
                tipo_erro=origem,
                descricao=descricao,
                gravidade=gravidade,
                remessa_id=remessa_id,
                dados_extra=dados_extra,
            ))

        return evento

    async def _enviar_alerta_email(
        self,
        tipo_erro: str,
        descricao: str,
        gravidade: str,
        remessa_id: int | None,
        dados_extra: dict | None,
    ) -> None:
        try:
            from core.email_service import enviar_alerta_erro
            remessa_numero = (dados_extra or {}).get("remessa_numero") or (
                str(remessa_id) if remessa_id else None
            )
            await enviar_alerta_erro(
                tipo_erro=tipo_erro,
                descricao=descricao,
                gravidade=gravidade,
                remessa_numero=remessa_numero,
            )
        except Exception as exc:
            logger.error("Falha ao disparar alerta de e-mail: %s", exc)
