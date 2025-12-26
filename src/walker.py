import os
import re
import logging
from typing import List, Set, Callable, Optional

logger = logging.getLogger(__name__)

def resolve_path(base_dir: str, current_file: str, relative_path: str) -> Optional[str]:
    """
    Resolves the absolute path of an included file.
    Handles omission of .tex extension.
    """
    # TeX \input paths are relative to the main file or current dir? 
    # Usually relative to PWD (which is project root) OR relative to current file location.
    # latexmk usually runs from root. We assume paths are relative to project root or set inputs.
    # But usually \input{chapters/intro} means ./chapters/intro.tex
    
    # Check if has extension
    if not relative_path.endswith('.tex'):
        candidate = relative_path + '.tex'
    else:
        candidate = relative_path

    # Check relative to current file dir
    current_dir = os.path.dirname(current_file)
    path_from_current = os.path.join(current_dir, candidate)
    if os.path.exists(path_from_current):
        return os.path.normpath(path_from_current)
    
    # Check relative to base_dir (project root)
    path_from_root = os.path.join(base_dir, candidate)
    if os.path.exists(path_from_root):
        return os.path.normpath(path_from_root)
        
    return None

def find_main_tex(sandbox_dir: str) -> Optional[str]:
    """
    Heuristic to find the main .tex file.
    1. Look for file with \documentclass.
    2. Prefer 'main.tex', 'paper.tex', 'ms.tex'.
    """
    candidates = []
    
    # Walk all files
    for root, dirs, files in os.walk(sandbox_dir):
        for f in files:
            if f.endswith('.tex'):
                full_path = os.path.join(root, f)
                with open(full_path, 'r', encoding='utf-8', errors='ignore') as d:
                    content = d.read()
                    if '\\documentclass' in content:
                        candidates.append(full_path)
    
    if not candidates:
        return None
    
    # Priority
    priorities = ['main.tex', 'paper.tex', 'article.tex']
    for p in priorities:
        for c in candidates:
            if os.path.basename(c).lower() == p:
                return c
                
    # Default to first candidate
    return candidates[0]

def walk_and_process(
    sandbox_dir: str, 
    entry_file: str, 
    process_callback: Callable[[str], None]
):
    """
    Traverses the project starting from entry_file using DFS.
    Calls process_callback(file_path) for each file.
    """
    visited: Set[str] = set()
    
    def dfs(current_path: str):
        if current_path in visited:
            return
        visited.add(current_path)
        
        logger.info(f"Processing: {current_path}")
        
        # 1. Process this file (Translate it inplace)
        # Note: We process BEFORE diving into children, or AFTER?
        # Usually process children first? Or process this file?
        # Since we modify files inplace, order matters if we modify \input commands.
        # But we DO NOT modify \input commands. We just translate content.
        # So we can process this file first.
        try:
            process_callback(current_path)
        except Exception as e:
            logger.error(f"Error processing {current_path}: {e}")
            
        # 2. Find children
        # We need to read the file AGAIN because it might have been modified? 
        # No, the translation should preserve \input tags.
        # But to be safe, read current content on disk.
        if not os.path.exists(current_path):
            return 
            
        with open(current_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            
        # Regex for input/include
        # \input{...} or \include{...}
        # Be careful of commented lines. Parser is safer but regex is faster for discovery.
        # We'll use simple Regex for discovery as implemented in plan.
        
        includes = re.findall(r'\\(?:input|include)\s*\{([^}]+)\}', content)
        
        for inc_path in includes:
            full_inc_path = resolve_path(sandbox_dir, current_path, inc_path)
            if full_inc_path:
                dfs(full_inc_path)
            else:
                logger.warning(f"Could not resolve include: {inc_path} in {current_path}")

    dfs(entry_file)
