"""Seed a number's knowledge base for tests."""

from __future__ import annotations

from tn_assist.paths import Workspace


def seed_knowledge(ws: Workspace, *, internal_canary: str = "INTERNAL-CANARY-7731") -> None:
    files = {
        "INDEX.md": (
            "- company.md: quem somos, horario e endereco\n"
            "- pricing.md: planos e precos, mensal e anual\n"
            "- faq.md: duvidas frequentes sobre entrega e reembolso\n"
            "- tone.md: como falar com os clientes\n"
            "- internal/margins.md: custo do fornecedor e margens\n"
        ),
        "company.md": "Loja Aurora, aberta de segunda a sexta, das 9h as 18h.\n",
        "pricing.md": "Plano mensal: R$ 49. Plano anual: R$ 490.\n",
        "faq.md": "Entrega em ate 5 dias uteis. Reembolso em ate 7 dias.\n",
        "tone.md": "Fale de forma simpatica e curta.\n",
        "internal/margins.md": f"{internal_canary}: margem de 62% no plano anual.\n",
    }
    for name, text in files.items():
        path = ws.path("knowledge", name)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
