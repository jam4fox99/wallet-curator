import os
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHARP_LOGS_DIR = BASE_DIR / 'data' / 'sharp_logs'
SIMS_DIR = BASE_DIR / 'data' / 'sims'
MALFORMED_DIR = BASE_DIR / 'data' / 'malformed'


def scan_sharp_logs(directory=None):
    """Return unprocessed .csv files in sharp_logs directory."""
    d = Path(directory) if directory else SHARP_LOGS_DIR
    files = []
    for f in d.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() != '.csv':
            continue
        name = f.name.lower()
        if name.startswith('ingested_') or name.startswith('processed_'):
            continue
        files.append(f)
    return sorted(files)


def scan_sims(directory=None):
    """Return unprocessed .xlsx files in sims directory."""
    d = Path(directory) if directory else SIMS_DIR
    files = []
    for f in d.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() != '.xlsx':
            continue
        if f.name.lower().startswith('sim_'):
            continue
        files.append(f)
    return sorted(files)


def scan_malformed(directory=None):
    """Return unprocessed .txt files in malformed directory."""
    d = Path(directory) if directory else MALFORMED_DIR
    if not d.exists():
        return []
    files = []
    for f in d.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() != '.txt':
            continue
        if f.name.lower().startswith('processed_'):
            continue
        files.append(f)
    return sorted(files)


def rename_processed(filepath, timestamp=None):
    """Rename a file with processed_ prefix and timestamp."""
    filepath = Path(filepath)
    if timestamp is None:
        timestamp = datetime.now()
    date_str = timestamp.strftime('%Y-%m-%d')
    time_str = timestamp.strftime('%H%M%S')
    stem = filepath.stem
    suffix = filepath.suffix
    new_name = f"processed_{stem}_{date_str}_{time_str}{suffix}"
    new_path = filepath.parent / new_name
    # Handle collision
    counter = 1
    while new_path.exists():
        new_name = f"processed_{stem}_{date_str}_{time_str}_{counter}{suffix}"
        new_path = filepath.parent / new_name
        counter += 1
    filepath.rename(new_path)
    logger.info("Renamed %s -> %s", filepath.name, new_path.name)
    return new_path


def create_archive(trades_df, batch_num, date=None, directory=None):
    """Save new-trades-only to ingested_NNN_date.csv."""
    d = Path(directory) if directory else SHARP_LOGS_DIR
    if date is None:
        date = datetime.now()
    date_str = date.strftime('%Y-%m-%d')
    filename = f"ingested_{batch_num:03d}_{date_str}.csv"
    path = d / filename
    trades_df.to_csv(path, index=False)
    logger.info("Created archive: %s (%d trades)", filename, len(trades_df))
    return path


def rename_sim(filepath, sim_number):
    """Rename sim file with sim_NNN_ prefix."""
    filepath = Path(filepath)
    new_name = f"sim_{sim_number:03d}_{filepath.name}"
    new_path = filepath.parent / new_name
    filepath.rename(new_path)
    logger.info("Renamed %s -> %s", filepath.name, new_path.name)
    return new_path


def save_malformed(header_line, malformed_lines, batch_num, date=None, directory=None):
    """Save malformed rows to data/malformed/ with header as first line."""
    d = Path(directory) if directory else MALFORMED_DIR
    os.makedirs(d, exist_ok=True)
    if date is None:
        date = datetime.now()
    date_str = date.strftime('%Y-%m-%d')
    filename = f"malformed_{batch_num:03d}_{date_str}.txt"
    path = d / filename
    with open(path, 'w') as f:
        f.write(header_line.rstrip('\n') + '\n')
        for line in malformed_lines:
            f.write(line.rstrip('\n') + '\n')
    logger.info("Saved %d malformed rows to %s", len(malformed_lines), filename)
    return path
