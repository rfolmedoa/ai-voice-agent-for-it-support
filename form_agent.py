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
from autogen_core.model_context import BufferedChatCompletionContext

# Optional convenience import – you can supply another client class if you wish.
from autogen_ext.models.openai import OpenAIChatCompletionClient
from jotform import JotformAPIClient
import os
from flask import Flask

# support_agent_prompt = '''
# Instructions:
# You are a friendly and efficient customer support agent who assists users over the phone in filling out the following form: {form_title}. Your primary goal is to guide the caller through each field in the form, collecting accurate information while maintaining a natural, conversational tone.

# Objectives:
# Only ask one question at a time. Use simple, polite, and conversational language.
# Ensure accuracy and clarity.
# Handle variations and clarifications naturally.
# Keep the experience human and supportive.
# Use active listening cues (like “Got it,” “Thank you,” “Let me confirm that”) and offer help when confusion arises.
# Handle skips and non-answers gracefully.
# If the user wants to skip a field and it's optional, continue. If it's required, kindly explain why it's needed.
# Always call tools to verify the user and add answers to the form when you have the information to do so.

# Tone & Style:
# Empathetic, professional, and supportive
# Use short, natural spoken sentences
# Avoid robotic or overly formal language

# Warning:
# Do not provide any adidtional details about the question unless the user asks for it or the user does not provide enough information!
# Never mention the expected format or hints unless explicitly asked by the user!
# Never ask for information that has already been provided by the user in previous conversations!
# Always call tools when you have the information to do so, and never ask the user for information that you already have!
# ALWAYS CALL THE verify_identity tool WHEN THE USER HAS ALREADY PROVIDED THEIR NAME AND BIRTH DATE, BUT THEIR IDENTITY IS NOT VERIFIED, EVEN IF THE USER PROVIDED THEM IN DIFFERENT MESSAGES.
# '''

# ignore_examples = '''

# Example Interactions:

# Name Field
# Agent:
# “To get started, may I have your full name?”

# Caller:
# “Sure, it's Maria Gonzalez.”

# Agent:
# “Thank you!”

# ---

# Email Address
# Agent:
# “Can you give me the best email address for contacting you?”

# Caller:
# “It's maria.gonzalez82 at gmail dot com.”

# Agent:
# “Thanks, got it.”

# ---

# Skipping a Field
# Agent:
# “The next field asks for your company name. Would you like to provide that?”

# Caller:
# “I don't have a company.”

# Agent:
# “No problem. We'll leave that blank and move on.”

# ---

# Required Field Missing
# Agent:
# “We'll need your date of birth to complete this form. This is just to verify your identity—could you share that with me now?”

# '''

# qa_form_prompt = '''
# You will help the user fill out the form by guiding them question by question and adding the user's answers to the form using the add_question_answer tool.
# If the user asks or responds something unrelated to the form, you will first reply to the user's question or response, and then ask the user the current question.
# If a user requests to skip a question, skip the question and move on to the next question if the question is not required. If the question is required, ask the user to answer the current question.
# If the user does not answer a question or answers it incorrectly, ask the user again until the user answers the current question correctly.
# If the user wants to change a previous answer, use the update_question_answer tool to update the answer.
# Once the user has answered all the questions, you will use the submit_form tool to submit the form.

# Never ask the next question until you have called the add_question_answer tool to add the user's answer to the form.

# Answered Questions:
# {qa_state}

# Next Question to Ask:
# {next_question}

# Current Question for User to Answer:
# {current_question}

# Current Response from User:
# {user_response}
# '''

support_agent_prompt = '''
Instructions:
You are a friendly and efficient customer support agent who assists users over the phone in filling out the following form: {form_title}. Your primary goal is to guide the caller through each field in the form, collecting accurate information while maintaining a natural, conversational tone.

Tone & Style:
Empathetic, professional, and supportive
Use short, natural spoken sentences
Avoid robotic or overly formal language

Warning:
Do not provide any adidtional details about the question unless the user asks for it or the user does not provide enough information!
Never mention the expected format or hints unless explicitly asked by the user!
Never ask for information that has already been provided by the user in previous conversations!
Always call tools when you have the information to do so, and never ask the user for information that you already have!
ALWAYS CALL THE verify_identity tool WHEN THE USER HAS ALREADY PROVIDED THEIR NAME AND BIRTH DATE, BUT THEIR IDENTITY IS NOT VERIFIED, EVEN IF THE USER PROVIDED THEM IN DIFFERENT MESSAGES.
'''

qa_form_prompt = '''
Instructions:
Your task is to add answers from the user to the answered questions state using the add_question_answer tool. When next question is None, you will use the submit_form tool to submit the form.
If a user requests to skip a question, skip the question and move on to the next question if the question is not required. If the question is required, ask the user to answer the current question.
If the user does not answer a question or answers it incorrectly, ask the user again until the user answers the current question correctly.
Once the user has answered all the questions, you will use the submit_form tool to submit the form using the answers in the answered questions state.

You will never ask the next question until you have called the add_question_answer tool to add the user's answer to the answered questions state.
To add a question to the answered questions state and move on to the next question, you must call the add_question_answer tool.
Be sure to call the add_question_answer tool with the answer to the current question before replying to the user.
Be sure to submit the form using the submit_form tool when the next question is None.

If identity is not verified, you will use the verify_identity tool to verify the user's identity. Verifying the identity is required before answering any questions or using any tools.
To verify identity, you must ask the user for their first and last name and birth date, then call the verify_identity tool.
If the user has already mentioned their name or birth date, only ask for the missing information and use the previously provided information to verify the identity with the verify_identity tool.

Is User's Identity Verified:
{identity_verified}

Answered Questions State:
{qa_state}

Current Question for User to Answer:
{current_question}

Next Question to Ask After User Answers Current Question:
{next_question}

Current Response from User:
{user_response}
'''

# qa_form_prompt = '''
# Instructions:
# Your task is to add answers from the user to the answered questions state using the add_question_answer tool.
# When the next question is None and you added the answer to the current question with add_question_answer tool, you will use the submit_form tool to submit the form.
# If a user requests to skip a question, skip the question and move on to the next question if the question is not required. If the question is required, ask the user to answer the current question.
# If the user does not answer a question or answers it incorrectly, ask the user again until the user answers the current question correctly.
# Once the user has answered all the questions, you will use the submit_form tool to submit the form using the answers in the answered questions state.

# You will never ask the next question until you have called the add_question_answer tool to add the user's answer to the answered questions state.
# To add a question to the answered questions state and move on to the next question, you must call the add_question_answer tool.
# Be sure to call the add_question_answer tool with the answer to the current question before replying to the user.
# Do not submit the form until the next question is None.

# Answered Questions State:
# {qa_state}

# Next Question to Ask:
# {next_question}

# Current Question for User to Answer:
# {current_question}

# Current Response from User:
# {user_response}
# '''
# Problem description:  My account lost access to the servers. I need IT to 
class FormAgent(RoutedAgent):
    """
    A RoutedAgent wrapper around autogen_agentchat.agents.AssistantAgent that
    *dynamically* instantiates the model client.

    Parameters
    ----------
    model_client_kwargs : dict, optional
        Keyword arguments forwarded to `model_client_cls(**model_client_kwargs)`.
    **assistant_kwargs
        Every other keyword is passed unchanged to `AssistantAgent`.
        If you *explicitly* supply your own ``model_client`` inside
        ``assistant_kwargs`` it will be used and *model_client_cls/kwargs*
        are ignored.
    """
    def __init__(
        self,
        *,
        form_id: str = None,
        api_key: str = os.getenv("JOTFORM_API_KEY"),
        publish_topic_type: str = None,
        model_client_kwargs: Optional[Dict[str, Any]] = None,
        flask_app: Flask = None,
        **assistant_kwargs: Any,
    ) -> None:
        # Description and super constructor must be initialized first because other fields may depend on it.
        description: str = assistant_kwargs.pop(
            "description",
            "An agent that provides assistance with filling out a form using the provided tools to interact with the form.",
        )

        super().__init__(description) 

        if publish_topic_type:
            self.publish_topic_id = TopicId(publish_topic_type, self.id.type)
        else:
            self.publish_topic_id = DefaultTopicId()

        self.identity_verified = False

        # Initialize form fields
        self.form_submitted = False
        self.form_id = form_id

        self.jotform_client = JotformAPIClient(api_key)
        self.form_info = self.jotform_client.get_form(form_id)

        self.form_title = self.form_info.get("title", "No Title Provided")

        self.questions  = []
        form_items = self.jotform_client.get_form_questions(form_id)
        for qid, question in form_items.items():
            is_readonly = question.get("readonly", "Yes")
            if is_readonly != "No":
                continue
            
            name = question.get("name", "")
            if name: name = f"_{name}"

            self.questions.append(question)

        self.qa_state = {}
        self.current_question_index = 0
        self.current_question = self.questions[self.current_question_index]

        if len(self.questions) > 1:
            self.next_question = self.questions[self.current_question_index + 1]
        else:
            self.next_question = None

        # Initialize assistant fields
        self.name: str = assistant_kwargs.pop("name", self.id.type)

        self.model_client = assistant_kwargs.pop(
            "model_client",
            OpenAIChatCompletionClient(**(model_client_kwargs or {}))
        )

        self.system_message = assistant_kwargs.pop(
            "system_message",
            support_agent_prompt.format(form_title=self.form_title)
        )

        self.model_context = assistant_kwargs.pop(
            "model_context",
            BufferedChatCompletionContext(buffer_size=10)
        )

        # self.tools = [self.search_name, self.verify_identity, self.update_question_answer, self.submit_form]
        self.tools = [self.verify_identity, self.add_question_answer, self.submit_form]
        if "tools" in assistant_kwargs:
            self.tools.extend(assistant_kwargs.pop("tools"))
        
        reflect_on_tool_use = assistant_kwargs.pop("reflect_on_tool_use", True)
        model_client_stream = assistant_kwargs.pop("model_client_stream", False)

        self.assistant = AssistantAgent(
            name=self.name,
            description=description,
            model_client=self.model_client,
            system_message=self.system_message,
            model_context=self.model_context,
            tools=self.tools,
            reflect_on_tool_use=reflect_on_tool_use,
            model_client_stream=model_client_stream,
            **assistant_kwargs,
        )

        if flask_app:
            flask_app.run(host='0.0.0.0', port=3035, debug=False)

    @message_handler
    async def handle_text(self, message: TextMessage, ctx: MessageContext) -> Any:
        """Forward incoming content to the AssistantAgent and publish reply."""
        # print(f"AgentID: {self.id}")
        # print(f"message: {message.content}")
        next_question: str = None
        current_question: str = None

        if self.form_submitted:
            return None

        if self.next_question:
            next_question = f"{self.next_question['text']}\nDetails: {self.next_question}"

        if self.current_question:
            current_question = f"{self.current_question['text']}\nDetails: {self.current_question}"
        
        task_message = qa_form_prompt.format(
            identity_verified=self.identity_verified,
            qa_state=self.qa_state,
            next_question=next_question,
            current_question=current_question,
            user_response=message.content
        )
        # context = await self.model_context.get_messages()
        # print(f"Model Context: {context}")
        
        result = await self.assistant.run(task=task_message, cancellation_token=ctx.cancellation_token)

        # Extract the assistant’s last message (the final response)
        # print(f"result: {result}")
        # print(f"task_message: {task_message}")
        # print(f"qa_state: {self.qa_state}")

        if result.messages:
            response = result.messages[-1].content  # type: ignore[attr-defined]
            # print(f"{self.name}: {response}")

            await self.publish_message(TextMessage(content=response, source=self.name), self.publish_topic_id)

            return TextMessage(content=response, source=self.name)
        
        return None
    
    # def search_name(name: str) -> str:
    #     print(f"Search Name Tool Output: {name}")
    #     return name

    def verify_identity(self, first_name: str, last_name: str, birth_month: int, birth_day: int, birth_year: int) -> str:
        """
        Verifies the user's identity by checking the full name and birthdate of the user,  then adds the name to the form if the name question is the current question.

        Arguments:
            first_name: First name
            last_name: Last name
            birth_month: Birth month
            birth_day: Birth day
            birth_year: Birth year
        """
        print(f"verify_identity tool: first: {first_name}, last: {last_name}, date: {birth_month}/{birth_day}/{birth_year}")

        if "rafael" in first_name.lower() and "olmedo" in last_name.lower() and birth_day == 11 and birth_month == 11 and birth_year == 1995:
            self.identity_verified = True
            name_added_message = ""

            if self.current_question['name'] == "name":
                self.add_question_answer(self.current_question['qid'], {"first": first_name, "last": last_name})
                name_added_message = "Name was also added to the form."

            return f"Identity verified successfully. {name_added_message}"
        return "Identity verification failed."
 
    def add_question_answer(self, qid: str, answer: str | dict[str, str]) -> str:
        """
        Adds a question answer to the form. 

        Arguments:
            qid: Question ID
            answer: Answer to the question. If the question has subfields, the answer is a dictionary of subfield answers where the key is the subfield label and the value is the answer.

        """
        if not self.identity_verified:
            return "Identity is not verified. Please verify your identity before answering any questions."

        self.qa_state[qid] = answer
        self.current_question_index += 1
        self.current_question = self.next_question
        
        if self.current_question_index + 1 < len(self.questions):
            self.next_question = self.questions[self.current_question_index + 1]
        else:
            self.next_question = None

        # self.current_question = self.next_question
        # if self.questions:
        #     self.next_question = self.questions.pop(0)
        # else:
        #     self.next_question = None

        print(f"add_question_answer tool: {qid}, {answer}")

        return "Answer added successfully."
    
    # def update_question_answer(self, qid: str, answer: str | dict[str, str]) -> str:
    #     """
    #     Updates a question answer in the form. 

    #     Arguments:
    #         qid: Question ID
    #         answer: Answer to the question. If the question has subfields, the answer is a dictionary of subfield answers where the key is the subfield label and the value is the answer.
    #     """
    #     self.qa_state[qid] = answer
    #     print(f"update_question_answer: {qid}, {answer}")
    #     return "Form updated successfully."
   
    def submit_form(self) -> str:
        """
        Submits the form using the answers in the answered questions state.
        """
        if not self.identity_verified:
            return "Identity is not verified. Please verify your identity before submitting the form."

        if self.next_question:
            return "There are still questions to answer. Please ask the next question before submitting the form."

        submission_data = {}

        for qid, answer in self.qa_state.items():
            if isinstance(answer, dict):
                for sub_qid, sub_answer in answer.items():
                    submission_data[f"{qid}_{sub_qid}"] = sub_answer
            else:
                submission_data[qid] = answer

        submission_info = self.jotform_client.create_form_submission(self.form_id, submission_data)

        if submission_info.get("submissionID"):
            self.form_submitted = True

        print(f"submit_form tool: {submission_info}")
        return "Form submitted successfully."