
#!/usr/bin/env python3
"""Entry point for Senedd XML parsing pipeline."""
import os
import logging
import argparse
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

from src.pipeline import SeneddPipeline

load_dotenv()

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)

# Configuration
DB_URL = os.getenv("DATABASE_URL", "sqlite:///senedd.db")
DATA_DIR = Path(__file__).parent / "data"
XML_FILE = DATA_DIR / "260602_Plenary_Bilingual.xml"


def main():
    parser = argparse.ArgumentParser(
        description="Senedd XML → Speech Reconstruction Pipeline"
    )
    
    parser.add_argument(
        "--mode",
        choices=["full", "incremental"],
        default="full",
        help="Pipeline mode: 'full' (rebuild from scratch) or 'incremental' (append/update)"
    )
    
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DATA_DIR,
        help="Directory for XML files (default: ./data/)"
    )
    
    parser.add_argument(
        "--xml-file",
        type=Path,
        default=XML_FILE,
        help="XML file to process (for full mode, default: ./data/260602_Plenary_Bilingual.xml)"
    )
    
    parser.add_argument(
        "--keep-xml",
        action="store_true",
        help="Keep raw XML files after processing (default: delete)"
    )
    
    parser.add_argument(
        "--last-sync",
        type=str,
        help="Override last sync date (format: YYYY-MM-DD, only for incremental mode)"
    )
    
    args = parser.parse_args()
    
    # Initialize pipeline
    pipeline = SeneddPipeline(DB_URL)
    
    if args.mode == "full":
        print(f"Running full pipeline on {args.xml_file}")
        if not args.xml_file.exists():
            print(f"Error: XML file not found: {args.xml_file}")
            return
        pipeline.run_full_pipeline(args.xml_file)
    
    elif args.mode == "incremental":
        print(f"Running incremental pipeline from {args.data_dir}")
        
        # Parse last_sync override if provided
        last_sync = None
        if args.last_sync:
            try:
                last_sync = datetime.strptime(args.last_sync, "%Y-%m-%d")
            except ValueError:
                print(f"Error: Invalid date format '{args.last_sync}'. Use YYYY-MM-DD")
                return
        
        pipeline.run_incremental(
            data_dir=args.data_dir,
            keep_xml=args.keep_xml,
            last_sync_date=last_sync
        )


if __name__ == "__main__":
    main()
