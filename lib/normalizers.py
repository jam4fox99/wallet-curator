import logging

logger = logging.getLogger(__name__)

# Game normalization mappings
MARKET_PREFIX_MAP = {
    'Counter-Strike:': 'CS2',
    'LoL:': 'LOL',
    'Dota 2:': 'DOTA',
    'Valorant:': 'VALO',
}

SIM_DETAIL_MAP = {
    'Counter-Strike': 'CS2',
    'League of Legends': 'LOL',
    'Dota 2': 'DOTA',
    'Valorant': 'VALO',
}

WHITELIST_GAME_PATTERNS = {
    'CS2': ['cs2-', 'csgo', 'counter-strike'],
    'LOL': ['lol-'],
    'DOTA': ['dota2-', 'dota-2', 'dota'],
    'VALO': ['val-', 'valorant'],
}


def normalize_wallet(addr: str) -> str:
    return str(addr).strip().lower()


def normalize_token_id(token_id: str) -> str:
    """Normalize token ID to lowercase hex with 0x prefix.

    Handles: hex strings (0x...), pure decimal strings, and
    scientific notation from pandas float coercion (6.84e+76).
    """
    token_id = str(token_id).strip()

    if token_id.startswith('0x') or token_id.startswith('0X'):
        return token_id.lower()

    # Try pure integer string first (the expected case when dtype=str works)
    try:
        return hex(int(token_id)).lower()
    except ValueError:
        pass

    # Fallback: scientific notation from float coercion
    # WARNING: This path means dtype=str failed or wasn't applied.
    # float64 only has ~15 digits of precision — the resulting hex WILL BE WRONG
    try:
        val = int(float(token_id))
        logger.warning(
            "Token ID '%s' was in scientific notation — precision loss likely. "
            "Verify dtype=str is being enforced on ID columns.", token_id
        )
        return hex(val).lower()
    except (ValueError, OverflowError):
        pass

    logger.error("Unrecognizable token ID format: '%s'", token_id)
    return token_id.lower()


def token_id_to_decimal(hex_token: str) -> str:
    """Convert canonical hex token ID to decimal string for Gamma API queries."""
    return str(int(hex_token, 16))


def normalize_game(text: str, source: str = 'market') -> str:
    """Normalize game name to canonical form.

    Args:
        text: The game text to normalize.
        source: One of 'market' (Sharp log Market column prefix),
                'sim_detail' (sim Results Detail column),
                'whitelist' (active_wallets.csv market_whitelist).
    Returns:
        One of: CS2, LOL, DOTA, VALO, ESPORTS, UNKNOWN
    """
    if not text or str(text).strip() == '':
        return 'UNKNOWN'

    text = str(text).strip()

    if source == 'market':
        for prefix, game in MARKET_PREFIX_MAP.items():
            if text.startswith(prefix):
                return game
        logger.warning("Unknown game in market name: '%s'", text)
        return 'UNKNOWN'

    elif source == 'sim_detail':
        game = SIM_DETAIL_MAP.get(text)
        if game:
            return game
        logger.warning("Unknown game in sim detail: '%s'", text)
        return 'UNKNOWN'

    elif source == 'whitelist':
        if not text:
            return 'UNKNOWN'
        text_lower = text.lower()
        matched_games = set()
        for game, patterns in WHITELIST_GAME_PATTERNS.items():
            for pattern in patterns:
                if pattern in text_lower:
                    matched_games.add(game)
                    break
        if len(matched_games) >= 2:
            return 'ESPORTS'
        elif len(matched_games) == 1:
            return matched_games.pop()
        else:
            logger.warning("Unknown game in whitelist: '%s'", text)
            return 'UNKNOWN'

    else:
        logger.error("Unknown source type: '%s'", source)
        return 'UNKNOWN'
