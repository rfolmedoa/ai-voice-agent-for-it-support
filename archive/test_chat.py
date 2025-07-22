import asyncio
import asyncio
from autogen_core import SingleThreadedAgentRuntime, AgentId, TypeSubscription
from autogen_agentchat.messages import TextMessage
from autogen_core.models import ModelFamily

from form_agent import FormAgent

from typing import Any, Dict, Optional, Type

from autogen_core import (
    DefaultTopicId,
    TopicId,
    MessageContext,
    RoutedAgent,
    default_subscription,
    message_handler,
)
from autogen_core.models import ChatCompletionClient
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage  # or your own message class

from autogen_ext.models.openai import OpenAIChatCompletionClient
from jotform import JotformAPIClient
import os
from dotenv import load_dotenv

load_dotenv()

class UserAgent(RoutedAgent):
    def __init__(self, name: str = None, description: str = "The user agent.", agent_topic_type: str = None, user_topic_type: str = None) -> None:
        super().__init__(description)

        self.name = name if name else self.id.type

        if agent_topic_type:
            self.agent_topic_type = TopicId(agent_topic_type, self.id.type)
        else:
            self.publish_topic_id = DefaultTopicId()

        if user_topic_type:
            self.user_topic_type = TopicId(user_topic_type, self.id.type)
        else:
            self.user_topic_type = DefaultTopicId()

    @message_handler
    async def handle_user_task(self, message: TextMessage, ctx: MessageContext) -> Any:

        print()
        print(f"--> Assistant Message: {message.content}")
        print(f"{'-'*80}", flush=True)
        human_input = input("--> User Input: ")

        if human_input == "exit":
            return

        await self.publish_message(TextMessage(content=human_input, source=self.name), self.publish_topic_id)

        return TextMessage(content=human_input, source=self.name)
    
async def main():
    runtime = SingleThreadedAgentRuntime()

    await FormAgent.register(
        runtime,
        type="assistant",
        factory=lambda: FormAgent(
            api_key=os.getenv("JOTFORM_API_KEY"),
            form_id="251997111120854",  
            model_client_kwargs={
                "model": "gpt-4o",
                "api_key": os.getenv("OPENAI_API_KEY"),
            },
            reflect_on_tool_use=True,
            model_client_stream=False,
        ),
    )
    await UserAgent.register(
        runtime,
        type="user",
        factory=lambda: UserAgent(),
    )

    await runtime.add_subscription(TypeSubscription(topic_type="default", agent_type="assistant"))
    await runtime.add_subscription(TypeSubscription(topic_type="default", agent_type="user"))

    runtime.start()
    human_input = input("--> User Input: ")
    await runtime.send_message(TextMessage(content=human_input, source="user"), AgentId("assistant", "default"))
    await runtime.stop_when_idle()

    
if __name__ == "__main__":
    asyncio.run(main())