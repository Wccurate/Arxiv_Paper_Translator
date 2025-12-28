import re
import math
import json
import logging
import os
from typing import List, Dict, Optional, TypedDict, Any
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from src.prompts import TRANSLATOR_SYSTEM_PROMPT, CRITIC_SYSTEM_PROMPT, FIXER_SYSTEM_PROMPT
from src.parser import unmask_content

logger = logging.getLogger(__name__)

# --- State Definition ---
class TranslationState(TypedDict):
    original_text: str          # The full masked text of the file (or chunk if processed chunk-wise)
                                # WAITING: The plan said "Split masked text into manageable chunks". 
                                # So the Graph processes ONE CHUNK at a time? Or the whole file?
                                # "Node B: Chunking. Split the masked text... Node C: Translation Agent"
                                # If the graph handles the chunks, the State should hold the list of chunks.
                                # But typically LangGraph is good for the "Process" of one item. 
                                # Let's define the Graph to process ONE CHUNK. 
                                # High-level loop in `translator.py` will iter chunks and run graph?
                                # OR the Graph has a "Map-Reduce" style?
                                # Creating a Graph for EACH chunk is fine.
                                
    chunk_index: int
    original_chunk: str         # The input masked chunk
    translated_chunk: str       # The output translated chunk
    terminology: Dict[str, str] # Context
    failed_attempts: int        # Counter for Fixer
    critic_errors: List[str]    # Feedback
    final_output: str           # Result

# --- Helper: Robust Chunking ---
def smart_split(text: str, max_chars: int = 4000) -> List[str]:
    """
    Splits text by \n\n but respects [MASK] tokens.
    """
    # 1. Split by double newline (paragraphs)
    paragraphs = re.split(r'(\n\n+)', text)
    
    chunks = []
    current_chunk = []
    current_length = 0
    
    for para in paragraphs:
        # Check if adding this paragraph exceeds limit
        if current_length + len(para) > max_chars and current_chunk:
            # Join and add to chunks
            chunks.append("".join(current_chunk))
            current_chunk = [para]
            current_length = len(para)
        else:
            current_chunk.append(para)
            current_length += len(para)
    
    if current_chunk:
        chunks.append("".join(current_chunk))
        
    return chunks

# --- Nodes ---

def translate_node(state: TranslationState):
    """
    Node C: Translation Agent
    """
    logger.debug(f"Translating chunk {state.get('chunk_index', 0)}")
    
    original = state['original_chunk']
    terminology = json.dumps(state['terminology'], ensure_ascii=False)
    
    model_name = os.getenv("MODEL_NAME", "gpt-4o")
    base_url = os.getenv("OPENAI_BASE_URL")
    llm = ChatOpenAI(model=model_name, temperature=0.3, base_url=base_url)
    
    # Construct messages directly to handle system prompt variables safely
    
    formatted_system = TRANSLATOR_SYSTEM_PROMPT.format(terminology=terminology)
    messages = [
        ("system", formatted_system),
        ("user", original)
    ]
    
    try:
        response = llm.invoke(messages)
        translated_text = response.content.strip()
        return {"translated_chunk": translated_text, "failed_attempts": 0}
    except Exception as e:
        logger.error(f"Translation LLM call failed: {e}")
        # Fail gracefully by returning original text (or we could retry)
        return {"translated_chunk": original, "failed_attempts": 999, "critic_errors": ["LLM Call Failed"]}

def critic_node(state: TranslationState):
    """
    Node D: Critic
    """
    logger.debug("Running Critic")
    
    original = state['original_chunk']
    translated = state['translated_chunk']
    
    model_name = os.getenv("MODEL_NAME", "gpt-4o")
    base_url = os.getenv("OPENAI_BASE_URL")
    llm = ChatOpenAI(model=model_name, temperature=0.0, base_url=base_url) # Critic needs to be strict
    prompt = PromptTemplate.from_template(CRITIC_SYSTEM_PROMPT)
    chain = prompt | llm
    
    try:
        response = chain.invoke({
            "original_chunk": original,
            "translated_chunk": translated
        })
        content = response.content.strip()
        
        # Parse JSON
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].strip()
            
        result = json.loads(content)
        
        is_safe = result.get("safe", False)
        is_syntax = result.get("syntax_valid", False)
        is_quality = result.get("quality_pass", False)
        errors = result.get("errors", [])
        
        if is_safe and is_syntax and is_quality:
            return {"critic_errors": []}
        else:
            if not errors:
                errors.append("Unknown failure")
            return {"critic_errors": errors}
            
    except Exception as e:
        logger.error(f"Critic failed to parse: {e}")
        # If critic fails, we warn but maybe proceed? Or force retry?
        # Unsafe to proceed. 
        return {"critic_errors": ["Critic parsing failed"]}

def fixer_node(state: TranslationState):
    """
    Node D (Branch): Fixer
    """
    failures = state.get("failed_attempts", 0) + 1
    logger.info(f"Fixer running. Attempt {failures}")
    
    if failures > 3:
        # Fallback to original text if too many failures
        logger.warning(f"Too many failures ({failures}). Reverting to original text.")
        return {
            "translated_chunk": state["original_chunk"], # Fallback
            "failed_attempts": failures,
            "critic_errors": [] # Clear errors to exit loop
        }

    original = state['original_chunk']
    translated = state['translated_chunk']
    errors = json.dumps(state['critic_errors'], ensure_ascii=False)
    terminology = json.dumps(state['terminology'], ensure_ascii=False)
    
    model_name = os.getenv("MODEL_NAME", "gpt-4o")
    base_url = os.getenv("OPENAI_BASE_URL")
    llm = ChatOpenAI(model=model_name, temperature=0.2, base_url=base_url)
    prompt = PromptTemplate.from_template(FIXER_SYSTEM_PROMPT)
    chain = prompt | llm
    
    try:
        response = chain.invoke({
            "original_chunk": original,
            "translated_chunk": translated,
            "errors": errors,
            "terminology": terminology
        })
        fixed_text = response.content.strip()
        return {"translated_chunk": fixed_text, "failed_attempts": failures}
    except Exception as e:
        logger.error(f"Fixer LLM call failed: {e}")
        # Return current bad translation (or original) and increment failures
        # If we return normally, it goes to critic -> critic fails -> fixer -> loop until max attempts.
        return {"translated_chunk": translated, "failed_attempts": failures}

# --- Conditional Logic ---
def check_critic(state: TranslationState):
    errors = state.get("critic_errors", [])
    if not errors:
        return "pass"
    return "fail"

# --- Graph Construction ---
def build_graph():
    workflow = StateGraph(TranslationState)
    
    workflow.add_node("translate", translate_node)
    workflow.add_node("critic", critic_node)
    workflow.add_node("fixer", fixer_node)
    
    workflow.set_entry_point("translate")
    
    workflow.add_edge("translate", "critic")
    
    workflow.add_conditional_edges(
        "critic",
        check_critic,
        {
            "pass": END,
            "fail": "fixer"
        }
    )
    
    workflow.add_edge("fixer", "critic") # Loop back to critic
    
    return workflow.compile()

# --- Main Interface ---
def translate_file_content(masked_content: str, terminology: Dict[str, str]) -> str:
    """
    Orchestrates the chunking and translation of a file's content.
    """
    # 1. Chunking
    chunks = smart_split(masked_content)
    translated_chunks = []
    
    app = build_graph()
    
    logger.info(f"File split into {len(chunks)} chunks.")
    
    for i, chunk in enumerate(chunks):
        # Skip empty chunks
        if not chunk.strip():
            translated_chunks.append(chunk)
            continue
            
        initial_state = {
            "original_chunk": chunk,
            "chunk_index": i,
            "terminology": terminology,
            "failed_attempts": 0,
            "critic_errors": [],
            # Initialize other required keys for TypeDict but LangGraph handles partials?
            # StateGraph requires keys to match if strictly typed? 
            # Usually strict=False default. 
        }
        
        result = app.invoke(initial_state)
        translated_chunks.append(result["translated_chunk"])
        
    # Join
    return "".join(translated_chunks)
