import os
import sys
import argparse
import shutil
import logging
import json
import tarfile
import zipfile
import arxiv
from dotenv import load_dotenv
from typing import Optional

# Load env
load_dotenv()

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("translation.log")
    ]
)
logger = logging.getLogger(__name__)

from src.context import extract_metadata, generate_terminology
from src.parser import mask_content, unmask_content
from src.translator import translate_file_content
from src.walker import walk_and_process, find_main_tex
from src.compiler import inject_fonts, compile_tex, sanitize_project

CONFIG_OUTPUT_DIR = "output"

def download_arxiv_source(arxiv_id: str, dest_dir: str):
    """Downloads ArXiv source tarball and extracts it."""
    try:
        logger.info(f"Downloading ArXiv ID: {arxiv_id}")
        client = arxiv.Client()
        paper = next(client.results(arxiv.Search(id_list=[arxiv_id])))
        
        tar_path = paper.download_source(dirpath=dest_dir, filename=f"{arxiv_id}.tar.gz")
        logger.info(f"Downloaded source to {tar_path}")
        
        # Extract
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(path=dest_dir)
            
        os.remove(tar_path) # Clean up tar
        logger.info("Extraction complete.")
        
    except Exception as e:
        logger.error(f"Failed to download/extract ArXiv source: {e}")
        sys.exit(1)

def extract_local_source(path: str, dest_dir: str):
    """Extracts local zip or copies folder."""
    if os.path.isfile(path):
        if path.endswith('.zip'):
             with zipfile.ZipFile(path, 'r') as zip_ref:
                zip_ref.extractall(dest_dir)
        elif path.endswith('.tar.gz') or path.endswith('.tgz'):
             with tarfile.open(path, "r:gz") as tar:
                tar.extractall(path=dest_dir)
        else:
            logger.error("Unsupported file format. Use zip or tar.gz")
            sys.exit(1)
    elif os.path.isdir(path):
        shutil.copytree(path, dest_dir, dirs_exist_ok=True)
    else:
        logger.error(f"Path not found: {path}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="ArXiv/LaTeX Auto-Translation System")
    parser.add_argument("--arxiv", type=str, help="ArXiv ID (e.g., 2310.xxxxx)")
    parser.add_argument("--local", type=str, help="Local path to source zip/folder")
    parser.add_argument("--model", type=str, default=os.getenv("MODEL_NAME", "gpt-4o"), help="LLM Model to use")
    parser.add_argument("--skip-translation", action="store_true", help="Skip translation and context phases, only compile")
    
    args = parser.parse_args()
    
    if not args.arxiv and not args.local:
        parser.print_help()
        sys.exit(1)
        
    project_name = args.arxiv if args.arxiv else os.path.basename(os.path.normpath(args.local)).split('.')[0]
    sandbox_dir = os.path.join(CONFIG_OUTPUT_DIR, project_name, "source_zh")
    logs_dir = os.path.join(CONFIG_OUTPUT_DIR, project_name, "logs")
    
    # Clean setup
    if os.path.exists(sandbox_dir):
        shutil.rmtree(sandbox_dir)
    os.makedirs(sandbox_dir, exist_ok=True)
    os.makedirs(logs_dir, exist_ok=True)
    
    # 1. Input Setup
    if args.arxiv:
        download_arxiv_source(args.arxiv, sandbox_dir)
    else:
        extract_local_source(args.local, sandbox_dir)
        
    # 2. Context Phase
    if not args.skip_translation:
        main_tex = find_main_tex(sandbox_dir)
        if not main_tex:
            logger.error("Could not find main.tex in the project!")
            sys.exit(1)
            
        logger.info(f"Main TeX file found: {main_tex}")
        
        with open(main_tex, 'r', encoding='utf-8', errors='ignore') as f:
            main_content = f.read()
            
        title, abstract = extract_metadata(main_content)
        logger.info(f"Title: {title}")
        
        terminology = {}
        if abstract:
            logger.info("Generating terminology from Abstract...")
            terminology = generate_terminology(abstract, args.model)
            logger.info(f"Terminology loaded: {len(terminology)} terms.")
            # Save terminology
            with open(os.path.join(logs_dir, "terminology.json"), 'w', encoding='utf-8') as f:
                json.dump(terminology, f, ensure_ascii=False, indent=2)
        else:
            logger.warning("No abstract found. Skipping terminology generation.")

        # 3. Translation Phase (Walker)
        
        def process_file_callback(file_path: str):
            logger.info(f"Translating {os.path.basename(file_path)}...")
            
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                
            # A. Mask
            masked_text, masks = mask_content(content)
            
            # B. Translate
            if masked_text.strip():
                translated_text = translate_file_content(masked_text, terminology)
            else:
                translated_text = masked_text
                
            # C. Unmask
            final_text = unmask_content(translated_text, masks)
            
            # Write back
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(final_text)
                
        logger.info("Starting recursive translation...")
        walk_and_process(sandbox_dir, main_tex, process_file_callback)
    else:
        logger.info("Skipping translation phase...")
        main_tex = find_main_tex(sandbox_dir)
        if not main_tex:
            logger.error("Could not find main.tex in the project!")
            sys.exit(1)
    
    # 4. Compilation Phase
    logger.info("Sanitizing project fonts...")
    sanitize_project(sandbox_dir)

    logger.info("Injecting fonts...")
    inject_fonts(main_tex)
    
    logger.info("Compiling PDF...")
    success, output = compile_tex(sandbox_dir, main_tex)
    
    if success:
        logger.info(f"Success! PDF generated at {os.path.join(sandbox_dir, os.path.basename(main_tex).replace('.tex', '.pdf'))}")
    else:
        logger.error("Compilation failed. Check logs.")
        
    # Copy PDF to output root for easy access
    pdf_name = os.path.basename(main_tex).replace('.tex', '.pdf')
    pdf_src = os.path.join(sandbox_dir, pdf_name)
    if os.path.exists(pdf_src):
        shutil.copy(pdf_src, os.path.join(CONFIG_OUTPUT_DIR, project_name, "paper_zh.pdf"))

if __name__ == "__main__":
    main()
