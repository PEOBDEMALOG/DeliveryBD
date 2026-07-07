# peo_bd/agents/agente_assistente.py
# Agente de Assistente de Diagnóstico
# Responsabilidade: responde dúvidas de Carlos e Timóteo — seja sobre um tipo
# de erro específico do Painel de Diagnóstico, seja de forma geral sobre o
# estado atual da operação (contador de remessas, alertas ativos, últimos erros).

import logging

import anthropic

from core.config import settings

logger = logging.getLogger(__name__)

_INSTRUCOES_BASE = """Você é o Assistente de Diagnóstico do sistema PEO-BD da Emalog.
Seu papel é ajudar os operadores Carlos (CD Itajaí) e Timoteo (CD Osasco)
a entender o estado da operação e a resolver erros que aparecem no sistema.

Responda de forma direta, prática e em português.
Não invente informações. Se não souber, diga para contatar o suporte da Emalog.
Nunca mencione detalhes técnicos internos do sistema (stack traces, nomes de tabelas, etc.).
Máximo de 3 parágrafos por resposta."""


class AgenteAssistente:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def responder(
        self,
        conversa: list[dict],
        mensagem: str,
        tipo_erro: dict | None = None,
        historico_eventos: list[dict] | None = None,
        contexto_sistema: dict | None = None,
    ) -> str:
        """
        Dois modos, mutuamente exclusivos:
          - tipo_erro + historico_eventos: foco em um erro específico do catálogo.
          - contexto_sistema: visão geral do estado atual da operação.
        """
        if tipo_erro:
            linhas_historico = "\n".join(
                f"- {e['timestamp']}: {e['descricao']}" for e in (historico_eventos or [])
            ) or "- Sem ocorrências recentes registradas."

            system = f"""{_INSTRUCOES_BASE}

Erro atual sendo discutido:
- Tipo: {tipo_erro['codigo']}
- Descrição: {tipo_erro['descricao']}
- Gravidade: {tipo_erro['gravidade']}
- Ação sugerida pelo sistema: {tipo_erro['acao_sugerida']}

Histórico recente deste erro no sistema:
{linhas_historico}"""
        else:
            ctx = contexto_sistema or {}
            remessas = ctx.get("remessas_por_status", {})
            linhas_remessas = "\n".join(
                f"- {status}: {qtd}" for status, qtd in remessas.items() if qtd
            ) or "- Sem remessas registradas hoje."

            linhas_erros = "\n".join(
                f"- {e['timestamp']}: {e['descricao']}" for e in ctx.get("ultimos_erros", [])
            ) or "- Nenhum erro recente registrado."

            system = f"""{_INSTRUCOES_BASE}

Estado atual do sistema (hoje):
Remessas por status:
{linhas_remessas}

Alertas ativos: {ctx.get('total_alertas', 0)} (críticos: {ctx.get('alertas_criticos', 0)}, altos: {ctx.get('alertas_altos', 0)})

Últimos erros registrados no Painel de Diagnóstico:
{linhas_erros}"""

        messages = conversa + [{"role": "user", "content": mensagem}]

        response = self.client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=512,
            system=system,
            messages=messages,
        )
        return response.content[0].text
