# peo_bd/core/email_service.py
# Serviço de e-mail transacional via Resend — usado para alertas internos Emalog.
# SMTP (smtplib) permanece no agente_comunicador para programações às transportadoras.

import os
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

_RESEND_AVAILABLE = False
try:
    import resend as _resend_lib
    _RESEND_AVAILABLE = True
except ImportError:
    logger.warning("Biblioteca 'resend' não instalada — alertas por e-mail desabilitados.")

_API_KEY = os.getenv("RESEND_API_KEY", "")
_EMAIL_DESTINO = os.getenv("EMAIL_ALERTAS_EMALOG", "erick.antonio@emalog.com.br")

_COR_GRAVIDADE = {
    "critico": "#dc2626",
    "alerta":  "#d97706",
    "info":    "#2563eb",
}


def _html_alerta(tipo_erro: str, descricao: str, gravidade: str, remessa_numero: str | None) -> str:
    cor = _COR_GRAVIDADE.get(gravidade, "#6b7280")
    remessa_linha = (
        f"<p><strong>Remessa:</strong> {remessa_numero}</p>" if remessa_numero else ""
    )
    return f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;color:#1f2937">
  <div style="border-left:4px solid {cor};padding:16px;background:#f9fafb;border-radius:4px;margin-bottom:24px">
    <h2 style="margin:0 0 4px;color:{cor};font-size:18px">
      Alerta {gravidade.upper()} — {tipo_erro}
    </h2>
    <p style="margin:0;color:#6b7280;font-size:13px">PEO-BD · {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC</p>
  </div>
  <p><strong>Descrição:</strong> {descricao}</p>
  {remessa_linha}
  <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0">
  <p style="font-size:12px;color:#9ca3af">
    Este alerta foi gerado automaticamente pelo sistema PEO-BD (Emalog).<br>
    Para suporte, acesse o painel em <a href="http://localhost:8000">localhost:8000</a>.
  </p>
</body>
</html>
"""


def _enviar_sync(tipo_erro: str, descricao: str, gravidade: str, remessa_numero: str | None) -> None:
    """Executa o envio Resend de forma síncrona (rodado em thread separada)."""
    if not _RESEND_AVAILABLE:
        logger.warning("resend não disponível — e-mail de alerta não enviado.")
        return
    if not _API_KEY:
        logger.warning("RESEND_API_KEY não configurada — e-mail de alerta não enviado.")
        return

    _resend_lib.api_key = _API_KEY
    try:
        _resend_lib.Emails.send({
            "from": "PEO-BD <alertas@emalog.com.br>",
            "to": _EMAIL_DESTINO,
            "subject": f"[PEO-BD] Alerta {gravidade.upper()} — {tipo_erro}",
            "html": _html_alerta(tipo_erro, descricao, gravidade, remessa_numero),
        })
        logger.info("Alerta e-mail enviado: %s (%s) → %s", tipo_erro, gravidade, _EMAIL_DESTINO)
    except Exception as exc:
        logger.error("Falha ao enviar e-mail de alerta via Resend: %s", exc)


async def enviar_alerta_erro(
    tipo_erro: str,
    descricao: str,
    gravidade: str,
    remessa_numero: str | None = None,
) -> None:
    """
    Envia e-mail de alerta de forma não-bloqueante (thread pool).
    Nunca levanta exceção — falhas de e-mail são logadas e ignoradas.
    """
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _enviar_sync, tipo_erro, descricao, gravidade, remessa_numero
    )
