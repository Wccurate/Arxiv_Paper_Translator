import re
import logging
from typing import Dict, Tuple, List
from pylatexenc.latexwalker import LatexWalker, LatexEnvironmentNode, LatexMacroNode, LatexGroupNode, LatexCharsNode, LatexMathNode, get_default_latex_context_db
from pylatexenc.macrospec import EnvironmentSpec

logger = logging.getLogger(__name__)

def get_custom_context():
    """
    Returns a LatexContextDb with augmented environment definitions.
    """
    # Load default configs
    db = get_default_latex_context_db()
    
    # Add definitions for code environments to ensure options are parsed as args, not content.
    # lstlisting: \begin{lstlisting}[options] ...
    # minted: \begin{minted}[options]{language} ...
    # verbatim: \begin{verbatim} ... (usually no args, but we can be safe)
    
    # args parser string: '[' = optional square brackets, '{' = mandatory curly braces
    
    cat = 'arxiv_translator_extensions'
    
    db.add_context_category(
        cat,
        environments=[
            EnvironmentSpec('lstlisting', '['), 
            EnvironmentSpec('maxipage', '['), # occasionally used
            EnvironmentSpec('minted', '[{'),
        ],
        prepend=True # Priority over default if any
    )
    return db

def mask_content(text: str) -> Tuple[str, Dict[str, str]]:
    """
    Parses LaTeX text and replaces specific structures with mask tokens.
    Returns (masked_text, dictionary_of_masks).
    
    Target structures:
    - Display Math ($$..$$, \[..\], \begin{equation}..)
    - Inline Math ($..$) - IF complex. For now, we mask strict inline math too for safety.
    - Citations/Refs (\cite{..}, \ref{..}, \label{..})
    - Figures/Tables (environments) -> WE MUST EXTRACT CAPTION? 
      *Strategy refinement*: We will mask the WHOLE environment, then we might need a specialized logic later 
      to sub-extract caption. For now, let's treat figures/tables as BLACK BOX MASK to keep it simple, 
      unless the user requirement explicitly said "Extract \caption content, mask the rest".
      The user requirement said: "Extract \caption content for translation, mask the rest".
      This is complex with simple masking. 
      For this MVP, we will try to NOT mask the environment wrapper, but mask the inner content?
      Actually, robust parser approach: 
      Walk the tree. If specific environment validation, mask it. 
    """
    
    # We will use a hybrid approach. 
    # 1. First, use Regex for "easy" tokens to avoid parser overhead/errors on partial chunks?
    # No, the requirement says "Use pylatexenc for safe AST traversal".
    
    # However, pylatexenc parses the WHOLE document. If we pass chunks, it might fail.
    # But here 'text' is likely a whole file content or a large section.
    
    # Let's try to parse the whole text.
    
    masks = {}
    mask_counter = 0

    try:
        # Use custom context to parse args correctly
        db = get_custom_context()
        walker = LatexWalker(text, latex_context=db, tolerant_parsing=True)
        nodes, _, _ = walker.get_latex_nodes()
        
        # We need to reconstruct the string with masks. 
        # This is hard with just nodes list because we need to rebuild the file.
        # Alternative: Identify ranges to mask, then process string from end to start to replace.
        
        mask_ranges = [] # List of (start_pos, end_pos, type_hint)

        # Environments Configuration
        OPAQUE_ENVS = {'equation', 'align', 'gather', 'eqnarray', 'tabular', 'tikzpicture', 'axuodraw', 'algorithmic'} 
        # Transparent Envs (recurse): figure, table, center, itemize, enumerate, etc. (Default behavior)
        # Content-Masked (Code) Envs: Mask children content but expose wrapper
        CODE_ENVS = {'lstlisting', 'verbatim', 'minted'}

        def traverse_nodes(node_list):
            for node in node_list:
                # Math
                if isinstance(node, LatexMathNode):
                    mask_ranges.append((node.pos, node.pos + node.len, "MATH"))
                    continue
                
                # Macros: \cite, \ref, \label, \includegraphics, \input, \include
                if isinstance(node, LatexMacroNode):
                    if node.macroname in ['cite', 'ref', 'cref', 'label', 'input', 'include', 'includegraphics']:
                        mask_ranges.append((node.pos, node.pos + node.len, f"CMD_{node.macroname.upper()}"))
                        continue
                
                # Environments
                if isinstance(node, LatexEnvironmentNode):
                    env_name = node.environmentname
                    
                    # 1. OPAQUE: Mask the entire environment (Wrapper + Content)
                    if env_name in OPAQUE_ENVS or env_name.endswith('*'): # Handle figure*? No, figure* should be transparent.
                        # Exclude figure* and table* from opaque check if we want them transparent
                        if env_name not in ['figure*', 'table*'] and (env_name in OPAQUE_ENVS or env_name.endswith('*')):
                             # Check if it is really opaque or just star variant of opaque?
                             # Let's be specific.
                             if env_name in OPAQUE_ENVS:
                                 mask_ranges.append((node.pos, node.pos + node.len, f"ENV_{env_name.upper().replace('*', 'S')}"))
                                 continue
                             if env_name.replace('*', '') in OPAQUE_ENVS:
                                 mask_ranges.append((node.pos, node.pos + node.len, f"ENV_{env_name.upper().replace('*', 'S')}"))
                                 continue

                    # 2. CODE: Mask the content children, but expose the wrapper
                    if env_name in CODE_ENVS:
                        # Mask all children nodes (which is the code content)
                        # The wrapper \begin{...} \end{...} is defined by node.pos/len minus children?
                        # No, simpler: just iterate children and mask them.
                        # For lstlisting, tokens might be char nodes or incomplete.
                        # We mask the range of the nodelist.
                        if hasattr(node, 'nodelist') and node.nodelist:
                            # Start of first child to end of last child
                            start_c = node.nodelist[0].pos
                            end_c = node.nodelist[-1].pos + node.nodelist[-1].len
                            mask_ranges.append((start_c, end_c, f"CODE_{env_name.upper()}"))
                        continue

                    # 3. Transparent (figure, table, etc.): Just recurse.
                    # Do nothing here, let it fall through to recursion.
                    pass
                        
                # Recurse if it's a group or generic env NOT masked
                if hasattr(node, 'nodelist'):
                    traverse_nodes(node.nodelist)

        traverse_nodes(nodes)
        
        # Sort ranges by start pos descending to replace safely
        mask_ranges.sort(key=lambda x: x[0], reverse=True)
        
        # Merge overlapping ranges? 
        # Simple merge: if current start < prev start (already sorted desc), check end.
        # But we sort desc by start.
        # (100, 200), (50, 60) -> Disjoint.
        # (100, 200), (150, 180) -> Overlap (Child inside Parent).
        # We should skip child if parent is replaced.
        # Since we traverse tree:
        # If we added Parent (Opaque), we didn't recurse. So no children added.
        # If we Recursed (Transparent), we didn't add Parent.
        # So disjoint property holds for tree traversal logic?
        # EXCEPT: Regex regex fallback tokens? No using pure parser here.
        # BUT: Code Env mask (children range).
        # What if Code Env parsing made children overlap?
        # For simplicity, we assume logic is non-overlapping.
        
        # Replace
        working_text = list(text)
        
        last_replaced_start = float('inf') # Track to avoid overlaps if any
        
        for start, end, type_hint in mask_ranges:
            # Safety checks for indices
            if start < 0 or end > len(text): continue
            
            # Use 'last_replaced_start' (which is higher index) to ensure strict order
            # If current end > last_replaced_start, we have overlap (inner nested in outer that was processed later? no sorted desc)
            # Sorted descending start.
            # Range 1: (200, 300)
            # Range 2: (100, 150)
            # Range 3: (120, 130) -> Overlap?
            # If (100, 150) was added, did we add (120, 130)?
            # If (100,150) is OPAQUE, we didn't recurse.
            # So (120,130) shouldn't exist.
            
            original_segment = text[start:end]
            token = f"[MASK_{type_hint}_{mask_counter:04d}]"
            masks[token] = original_segment
            mask_counter += 1
            
            # Replace
            working_text[start:end] = list(token)
            
        return "".join(working_text), masks

    except Exception as e:
        logger.error(f"PyLaTeXenc parsing failed: {e}. Fallback to basic Regex masking.")
        return mask_content_regex_fallback(text)

def mask_content_regex_fallback(text: str) -> Tuple[str, Dict[str, str]]:
    """
    Fallback regex masker if parser fails.
    """
    masks = {}
    mask_counter = 0
    
    def replacer(match):
        nonlocal mask_counter
        original = match.group(0)
        token = f"[MASK_R_MATH_{mask_counter:04d}]"
        masks[token] = original
        mask_counter += 1
        return token

    # Mask display math $$...$$
    text = re.sub(r'\$\$.*?\$\$', replacer, text, flags=re.DOTALL)
    
    # Mask inline math $...$ (Basic) - avoiding escaped \$
    # This is fragile with regex but it's a fallback.
    text = re.sub(r'(?<!\\)\$.*?(?<!\\)\$', replacer, text, flags=re.DOTALL)
    
    return text, masks

def unmask_content(text: str, masks: Dict[str, str]) -> str:
    """
    Replaces tokens with original content.
    """
    # Simply replace all keys
    # To avoid substring issues, maybe sort keys by length desc?
    # Tokens are uniform length though.
    for token, original in masks.items():
        text = text.replace(token, original)
    return text
