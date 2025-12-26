import os
import re
import subprocess
import logging

logger = logging.getLogger(__name__)


def sanitize_tex_content(content: str) -> str:
    """
    Comments out conflicting font packages that interfere with xeCJK/xelatex.
    """
    # List of conflicting packages
    # fontenc T1 causes issues with CJK. inputenc utf8 is defaults in modern latex but ok to remove.
    # times, palatino, etc override the main font.
    conflicts = [
        r'usepackage\s*(\[.*?\])?\s*{times}',
        r'usepackage\s*(\[.*?\])?\s*{palatino}',
        r'usepackage\s*(\[.*?\])?\s*{mathptmx}',
        r'usepackage\s*(\[.*?\])?\s*{newtxtext}',
        r'usepackage\s*(\[.*?\])?\s*{newtxmath}',
        r'usepackage\s*\[T1\]\s*{fontenc}',
        r'usepackage\s*\[utf8\]\s*{inputenc}',
        r'usepackage\s*(\[.*?\])?\s*{helvet}',
        r'usepackage\s*(\[.*?\])?\s*{avant}',
        r'usepackage\s*(\[.*?\])?\s*{courier}',
        r'usepackage\s*(\[.*?\])?\s*{chancery}',
        r'usepackage\s*(\[.*?\])?\s*{bookman}',
        r'usepackage\s*(\[.*?\])?\s*{newcent}',
        r'usepackage\s*(\[.*?\])?\s*{charter}',
        r'usepackage\s*(\[.*?\])?\s*{fourier}'
    ]
    
    new_content = content
    
    # 1. Sanitize \pdfoutput (causes error in xelatex)
    # \pdfoutput=1 or similar
    regex_pdf = re.compile(r'^(\s*)(\\pdfoutput\s*=\s*\d+)', re.MULTILINE)
    new_content = regex_pdf.sub(r'\1% ARXIV_TRANSLATOR_SANITIZED: \2', new_content)

    for pattern in conflicts:
        # Regex replacement: Comment out the line
        # We look for \usepackage... matching the pattern
        # We replace with % \usepackage...
        # Need to handle potential whitespaces and Ensure we don't double comment
        
        # Regex: (optional whitespace) ( \usepackage ... )
        regex = re.compile(r'^(\s*)(\\' + pattern + r'.*)$', re.MULTILINE)
        new_content = regex.sub(r'\1% ARXIV_TRANSLATOR_SANITIZED: \2', new_content)
        
    return new_content

def sanitize_project(sandbox_dir: str):
    """
    Recursively scans .tex, .sty, .cls files in sandbox_dir and sanitizes them.
    """
    logger.info(f"Sanitizing font usage in {sandbox_dir}...")
    
    extensions = {'.tex', '.sty', '.cls'}
    
    for root, dirs, files in os.walk(sandbox_dir):
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in extensions:
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    
                    sanitized = sanitize_tex_content(content)
                    
                    if sanitized != content:
                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(sanitized)
                        logger.debug(f"Sanitized {file}")
                        
                except Exception as e:
                    logger.warning(f"Failed to sanitize {file_path}: {e}")

def inject_fonts(main_tex_path: str):

    """
    Injects xeCJK and font settings into main.tex.
    Placement: Immediately after \documentclass{...}.
    """
    if not os.path.exists(main_tex_path):
        logger.error(f"main.tex not found at {main_tex_path}")
        return

    with open(main_tex_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Define injection block
    # Using SimsSun/SimHei as requested. 
    # Ensure standard fallback if not present? 
    # For now, hardcode standard fonts assumed to be available or added.
    # Detect Platform
    import sys
    
    if sys.platform == 'darwin':
        # macOS Fonts
        font_setup = r"""
% --- Auto-Translation Font Injection (macOS) ---
\usepackage{xeCJK}
\setCJKmainfont[BoldFont=Songti SC Bold, ItalicFont=Songti SC Light]{Songti SC}
\setCJKsansfont{Heiti SC}
\setCJKmonofont{STFangsong}
% ---------------------------------------------
"""
    else:
        # Standard/Windows Fonts (SimSun) or Linux (Fandol if available, but sticking to requested)
        font_setup = r"""
% --- Auto-Translation Font Injection ---
\usepackage{xeCJK}
\setCJKmainfont{SimSun}
\setCJKsansfont{SimHei}
\setCJKmonofont{FangSong}
% ---------------------------------------
"""
    
    injection = font_setup
    
    # Regex find \documentclass
    # \documentclass[options]{class} or \documentclass{class}
    pattern = re.compile(r'(\\documentclass(\[.*?\])?\{.*?\})', re.DOTALL)
    match = pattern.search(content)
    
    if match:
        end_pos = match.end(1)
        new_content = content[:end_pos] + injection + content[end_pos:]
        
        with open(main_tex_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        logger.info("Injected font settings.")
    else:
        logger.warning("Could not find \\documentclass to inject fonts. Prepending to file.")
        with open(main_tex_path, 'w', encoding='utf-8') as f:
            f.write(injection + content)

def compile_tex(sandbox_dir: str, main_tex_path: str):
    """
    Runs latexmk -xelatex in the sandbox.
    """
    main_file = os.path.basename(main_tex_path)
    
    cmd = [
        "latexmk",
        "-xelatex",
        "-interaction=nonstopmode",
        "-file-line-error",
        "-halt-on-error",
        main_file
    ]
    
    logger.info(f"Compiling {main_file} in {sandbox_dir}")
    
    try:
        # Run process
        result = subprocess.run(
            cmd,
            cwd=sandbox_dir,
            capture_output=True,
            text=True,
            timeout=300 # 5 min timeout
        )
        
        if result.returncode != 0:
            logger.error("Compilation failed.")
            logger.error(result.stdout)
            logger.error(result.stderr)
            # Find log file
            log_file = main_file.replace('.tex', '.log')
            log_path = os.path.join(sandbox_dir, log_file)
            if os.path.exists(log_path):
                logger.info(f"Log file available at {log_path}")
            return False, result.stdout + result.stderr
            
        logger.info("Compilation successful.")
        return True, result.stdout
        
    except subprocess.TimeoutExpired:
        logger.error("Compilation timed out.")
        return False, "Timeout"
    except Exception as e:
        logger.error(f"Compilation error: {e}")
        return False, str(e)
