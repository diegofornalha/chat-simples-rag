import asyncio
from claude_agent_sdk import query, AssistantMessage, TextBlock

async def main():
    """
    Função principal que conversa com o Claude.

    O 'async' significa que essa função pode "esperar" por operações
    que demoram (como chamadas de rede para a API do Claude).
    """

    print("Iniciando Hello Agent...")
    print("-" * 50)

    async for message in query(prompt="Diga: Olá! Sou seu primeiro agente."):

        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(f"Claude: {block.text}")

    print("-" * 50)
    print("Agente finalizado!")

if __name__ == "__main__":
    asyncio.run(main())
