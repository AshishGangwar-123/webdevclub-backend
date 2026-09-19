import os
import json
import re
from typing import List, Dict, Any
from dotenv import load_dotenv

load_dotenv()

# LangChain Imports
try:
    from langchain.prompts import PromptTemplate
    HAS_LANGCHAIN = True
except ImportError:
    HAS_LANGCHAIN = False

# LangChain PromptTemplate for Structured Test Question Generation
TEST_MAKER_PROMPT_TEMPLATE = """You are an expert technical examiner and lead educator for Web Development Club (WDC RECB).
Your task is to create a high-quality, comprehensive multiple-choice test for a workshop on the specified topic(s).

TEST SPECIFICATIONS:
- Workshop Topics: {topic}
- Number of Questions: {num_questions}
- Difficulty Level: {level} (Beginner, Intermediate, or Advanced)
- Question Type: {question_type} (single_correct or multi_correct)

RULES & CONSTRAINTS:
1. Provide EXACTLY valid JSON format matching the schema below.
2. Output NO markdown extra text outside the JSON array.
3. Every question must have EXACTLY 4 distinct option choices.
4. "correct_answers" MUST be a JSON list of 0-based option index/indices that are correct:
   - For single_correct: exactly one index, e.g. [0] or [1] or [2] or [3].
   - For multi_correct: 2 or more indices, e.g. [0, 2].
5. Provide a clear "explanation" for autocheck scoring.
6. Do not include the difficulty level, labels such as "Intermediate Level", or question numbers in the question text.

JSON SCHEMA OUTPUT FORMAT:
[
  {{
    "id": 1,
    "question": "Question text here...",
    "options": ["Option A", "Option B", "Option C", "Option D"],
    "correct_answers": [0],
    "explanation": "Detailed explanation of why Option A is correct."
  }}
]
"""

def generate_ai_test_questions(
    topic: str,
    num_questions: int = 5,
    level: str = "Intermediate",
    question_type: str = "single_correct"
) -> List[Dict[str, Any]]:
    """
    Generates test questions using LangChain PromptTemplate and LLM / Smart Fallback Engine.
    Returns a list of structured question objects with 4 options and marked correct answer keys.
    """
    topic_str = topic.strip() if topic else "Full-Stack Web Development & AI"
    num_q = max(1, min(int(num_questions or 5), 20))
    lvl = level if level in ["Beginner", "Intermediate", "Advanced"] else "Intermediate"
    q_type = "multi_correct" if "multi" in str(question_type).lower() else "single_correct"

    # Try LangChain Chat Model if API keys are available
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    if HAS_LANGCHAIN and (anthropic_key or openai_key):
        try:
            prompt = PromptTemplate(
                template=TEST_MAKER_PROMPT_TEMPLATE,
                input_variables=["topic", "num_questions", "level", "question_type"]
            )
            formatted_prompt = prompt.format(
                topic=topic_str,
                num_questions=num_q,
                level=lvl,
                question_type=q_type
            )

            # Try Anthropic / OpenAI via LangChain or direct LLM
            raw_response = None
            if anthropic_key:
                from langchain_community.chat_models import ChatAnthropic
                llm = ChatAnthropic(anthropic_api_key=anthropic_key, model="claude-3-haiku-20240307", temperature=0.3)
                res = llm.invoke(formatted_prompt)
                raw_response = res.content if hasattr(res, 'content') else str(res)
            elif openai_key:
                from langchain_openai import ChatOpenAI
                llm = ChatOpenAI(openai_api_key=openai_key, model_name="gpt-3.5-turbo", temperature=0.3)
                res = llm.invoke(formatted_prompt)
                raw_response = res.content if hasattr(res, 'content') else str(res)

            if raw_response:
                # Clean JSON code blocks if present
                clean_json = re.sub(r'```json\s*', '', raw_response)
                clean_json = re.sub(r'```\s*$', '', clean_json).strip()
                parsed = json.loads(clean_json)
                if isinstance(parsed, list) and len(parsed) > 0:
                    return parsed
        except Exception as err:
            print("LangChain LLM call error, using smart fallback question generator:", err)

    # Smart High-Quality Fallback Question Generator
    return generate_fallback_questions(topic_str, num_q, lvl, q_type)


def generate_fallback_questions(topic: str, count: int, level: str, q_type: str) -> List[Dict[str, Any]]:
    """
    Generates realistic, accurate technical questions tailored to the input topic.
    """
    questions = []
    topic_clean = topic.strip().title()

    templates = [
        {
            "q": f"In {topic_clean}, what is the primary purpose of using asynchronous standard handlers?",
            "opts": [
                "To prevent blocking the main execution thread during long-running tasks",
                "To automatically encrypt network transmissions across nodes",
                "To compile client-side styles into WebAssembly bytecodes",
                "To increase memory allocation limits of virtual machine processes"
            ],
            "correct": [0],
            "exp": "Asynchronous handlers allow I/O operations and API requests to run concurrently without blocking the main event loop."
        },
        {
            "q": f"Which of the following best describes state management when building applications with {topic_clean}?",
            "opts": [
                "Directly modifying window global variables on every user click",
                "Maintaining predictable data flow using controlled state containers or hooks",
                "Storing state permanently inside CSS variables",
                "Rebuilding the entire application bundle on each user action"
            ],
            "correct": [1],
            "exp": "Modern architectures use unidirectional data flow and state containers/hooks to ensure reactive and predictable state updates."
        },
        {
            "q": f"When configuring performance optimizations in {topic_clean}, which practice is recommended?",
            "opts": [
                "Removing all caching mechanisms to save disk space",
                "Debouncing rapid user input events and lazy-loading heavy modules",
                "Executing heavy calculations synchronously on UI render threads",
                "Disabling CORS headers for all database connections"
            ],
            "correct": [1],
            "exp": "Debouncing input handlers and code-splitting/lazy-loading optimize render speed and reduce bundle size."
        },
        {
            "q": f"What is the key benefit of REST/GraphQL API integration when working on {topic_clean}?",
            "opts": [
                "Decoupling frontend UI from server database logic via clean data contracts",
                "Eliminating the need for backend validation or security checks",
                "Automatically converting SQL queries into HTML components",
                "Bypassing HTTP protocol standards completely"
            ],
            "correct": [0],
            "exp": "API layers decouple presentation code from server-side databases, enabling scalable multi-platform integration."
        },
        {
            "q": f"In {topic_clean} deployment and production setups, what is the purpose of environment configuration files (.env)?",
            "opts": [
                "Storing public HTML markup templates",
                "Safely storing sensitive API keys, DB connections, and environment flags",
                "Generating CSS grid styles at runtime",
                "Overriding browser user-agent security policies"
            ],
            "correct": [1],
            "exp": "Environment files (.env) store secrets and configuration variables outside the source code repository."
        },
        {
            "q": f"Which protocol or format is most commonly used for real-time bi-directional communication in {topic_clean}?",
            "opts": [
                "Static FTP file transfers",
                "WebSockets / Server-Sent Events (SSE)",
                "SMTP email headers",
                "Raw MP3 audio streams"
            ],
            "correct": [1],
            "exp": "WebSockets and SSE provide low-latency, full-duplex communication channels for real-time updates."
        },
    ]

    for i in range(count):
        tmpl = templates[i % len(templates)]
        
        if q_type == "multi_correct":
            # For multi correct, adjust question and correct options
            q_text = f"[Multi-Select] In {topic_clean} ({level} Level), which options represent valid principles/practices? (Select all true statements)"
            opts = [
                f"Principle A: Modular code structure improves reusability and testing in {topic_clean}",
                "Principle B: Hardcoding API keys directly in client-side code is secure",
                f"Principle C: Proper error handling prevents unexpected app crashes in {topic_clean}",
                "Principle D: Deleting unit tests increases application stability"
            ]
            correct = [0, 2]
            exp = "Modular architecture and comprehensive error handling are core best practices; hardcoding secrets and deleting tests are dangerous."
        else:
            q_text = tmpl['q']
            opts = tmpl['opts']
            correct = tmpl['correct']
            exp = tmpl['exp']

        questions.append({
            "id": i + 1,
            "question": q_text,
            "options": opts,
            "correct_answers": correct,
            "explanation": exp
        })

    return questions
