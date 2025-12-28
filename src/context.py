import re
import json
import logging
import os
from typing import Dict, Optional, Tuple
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from src.prompts import TERMINOLOGY_PROMPT

logger = logging.getLogger(__name__)

def extract_metadata(tex_content: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extracts the Title and Abstract from valid LaTeX content.
    Returns (Title, Abstract).
    """
    # Simple regex for title
    # \title{...} - handles multi-line basic braces
    title_pattern = re.compile(r'\\title\s*\{((?:[^{}]|\\{|\\}|(?:\{[^{}]*\}))*)\}', re.DOTALL)
    title_match = title_pattern.search(tex_content)
    title = title_match.group(1).strip() if title_match else None

    # Simple regex for abstract
    # \begin{abstract} ... \end{abstract}
    abstract_pattern = re.compile(r'\\begin\{abstract\}(.*?)\\end\{abstract\}', re.DOTALL)
    abstract_match = abstract_pattern.search(tex_content)
    abstract = abstract_match.group(1).strip() if abstract_match else None

    # Clean up TeX commands from title/abstract minimal
    if title:
        title = _clean_tex(title)
    if abstract:
        abstract = _clean_tex(abstract)

    return title, abstract

def _clean_tex(text: str) -> str:
    """Basic cleanup to remove comments and excessive whitespace."""
    # Remove lines starting with %
    lines = [l for l in text.splitlines() if not l.strip().startswith('%')]
    return ' '.join(lines).strip()

def generate_terminology(abstract: str, model_name: str = "gpt-4o") -> Dict[str, str]:
    """
    Calls LLM to generate terminology dictionary from abstract.
    """
    if not abstract:
        logger.warning("No abstract provided for terminology generation.")
        return {}

    try:
        base_url = os.getenv("OPENAI_BASE_URL")
        llm = ChatOpenAI(model=model_name, temperature=0.0, base_url=base_url)
        prompt = PromptTemplate.from_template(TERMINOLOGY_PROMPT)
        chain = prompt | llm

        response = chain.invoke({"abstract": abstract})
        content = response.content.strip()

        # Parse JSON
        # It usually returns code block ```json ... ```
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].strip()
        
        terminology = json.loads(content)
        return terminology
    except Exception as e:
        logger.error(f"Failed to generate terminology: {e}")
        return {}
