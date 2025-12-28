# ArXiv/LaTeX Auto-Translation System

A robust, multi-agent AI system for translating academic papers (LaTeX) from English to Chinese.
Preserves strict LaTeX syntax, equations, tables, and citations while ensuring terminology consistency.

## Features
- **Multi-Agent Architecture**: Uses specialized agents (Translator, Critic, Fixer) orchestrated by LangGraph.
- **Context-Aware**: Extracts title/abstract to generate a global terminology dictionary before translation.
- **Robust Parsing**: Hybrid masking using `pylatexenc` and Regex to protect math and commands.
- **Recursive Processing**: Handles multi-file LaTeX projects (`\input`, `\include`).
- **Safety Checks**: "Reflexion" loop verifies that all math/citation masks are preserved in the output.
- **Auto-Compilation**: Injects `xeCJK` fonts and compiles the translated source to PDF.

## Installation

1. Clone the repository.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Set up environment variables in `.env`:
   ```bash
   OPENAI_API_KEY=your_key_here
   MODEL_NAME=gpt-5-mini
   ```
   (See `.env.example`)

## Usage

### Translate an ArXiv Paper
```bash
python main.py --arxiv 2310.xxxxx
```

### Translate a Local Project
```bash
python main.py --local ./example_input
```

### Debugging / Quick Compilation
If you only want to re-compile the PDF without re-running the translation (e.g., to test font settings):
```bash
python main.py --local ./example_input --skip-translation
```

## Output
Results are saved in `output/{project_name}/`:
- `paper_zh.pdf`: Compiled Chinese PDF.
- `source_zh/`: Full translated source code.
- `logs/`: Terminology map and internal logs.

## Features & Robustness
- **Smart Font Sanitization**: Automatically detects and comments out conflicting legacy packages (`times`, `fontenc`, `inputenc`) and commands (`\pdfoutput`) to ensure successful compilation with `xeCJK`.
- **Fault Tolerance**: Contains retry logic for API limitations and compilation errors.

## Architecture
1. **Sandbox**: Copies source to `output/` to safe-guard original files.
2. **Context**: LLM extracts terminology from Abstract.
3. **Walk**: Recursively finds `.tex` files.
4. **Translate**:
   - **Mask**: Protects `$$...$$`, `\cite{}`, etc.
   - **Translate**: LLM translates text chunks using terminology.
   - **Critic**: Checks for missing masks or broken syntax.
   - **Fixer**: Auto-corrects errors if Critic fails.
   - **Unmask**: Restores protected content.
5. **Compile**: Runs `latexmk`.

## Dependencies
- Python 3.10+
- `latexmk` with `xelatex` (TeXLive/MacTeX installed)
- `langchain`, `langgraph`, `openai`
