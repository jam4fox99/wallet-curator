import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _get_monday(date_str):
    """Get the Monday of the week for a given date string."""
    try:
        if 'T' in date_str:
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        else:
            dt = datetime.fromisoformat(date_str)
        # Monday = 0
        monday = dt - timedelta(days=dt.weekday())
        return monday.strftime('%Y-%m-%d')
    except Exception:
        return None


def compute_weekly_pnl(conn, wallet, weeks=8):
    """Compute weekly P&L buckets for a wallet.

    P&L is attributed to the week of the LAST trade timestamp for that position.
    Returns list of {week_start, realized_pnl, markets_resolved, unrealized} for last N weeks.
    """
    # Get all positions with their resolution status and last trade date
    positions = conn.execute("""
        SELECT p.token_id, p.market, p.total_invested, p.total_shares_bought,
               p.total_shares_sold, p.total_received, p.net_shares, p.avg_cost_basis,
               r.resolved,
               (SELECT MAX(timestamp) FROM trades
                WHERE master_wallet = p.master_wallet AND token_id = p.token_id) as last_trade_ts
        FROM positions p
        LEFT JOIN resolutions r ON p.token_id = r.token_id
        WHERE p.master_wallet = ?
    """, (wallet,)).fetchall()

    weekly = {}  # monday_str -> {realized, markets_resolved, unrealized}

    for pos in positions:
        monday = _get_monday(pos['last_trade_ts']) if pos['last_trade_ts'] else None
        if not monday:
            continue

        if monday not in weekly:
            weekly[monday] = {'realized_pnl': 0.0, 'markets_resolved': 0, 'unrealized': 0.0}

        avg_cost = pos['avg_cost_basis']
        if not avg_cost:
            continue

        resolved = pos['resolved'] if pos['resolved'] is not None else 0
        net_shares = pos['net_shares']
        shares_sold = pos['total_shares_sold']
        received = pos['total_received']

        # Sell P&L
        sell_pnl = received - (avg_cost * shares_sold)
        weekly[monday]['realized_pnl'] += sell_pnl

        if resolved == 1:
            resolution_pnl = (net_shares * 1.0) - (avg_cost * net_shares)
            weekly[monday]['realized_pnl'] += resolution_pnl
            weekly[monday]['markets_resolved'] += 1
        elif resolved == -1:
            loss = avg_cost * net_shares
            weekly[monday]['realized_pnl'] -= loss
            weekly[monday]['markets_resolved'] += 1
        else:
            weekly[monday]['unrealized'] += avg_cost * net_shares

    # Sort by date and return last N weeks
    sorted_weeks = sorted(weekly.items(), key=lambda x: x[0], reverse=True)[:weeks]
    sorted_weeks.reverse()

    return [
        {
            'week_start': w[0],
            'realized_pnl': w[1]['realized_pnl'],
            'markets_resolved': w[1]['markets_resolved'],
            'unrealized': w[1]['unrealized'],
        }
        for w in sorted_weeks
    ]


def build_wallet_profile(conn, wallet):
    """Build a complete wallet profile for the evaluation prompt."""
    # Real P&L
    pnl = conn.execute("SELECT * FROM wallet_pnl WHERE master_wallet = ?", (wallet,)).fetchone()

    # Weekly buckets
    weekly = compute_weekly_pnl(conn, wallet)

    # Sim profile (latest sim)
    sim_profile = conn.execute("""
        SELECT sp.*, ss.sim_pnl, ss.sim_roi_pct, ss.trades as sim_total_trades,
               ss.lb_all_time, ss.lb_name
        FROM sim_profiles sp
        JOIN sim_snapshots ss ON sp.sim_number = ss.sim_number AND sp.wallet_address = ss.wallet_address
        WHERE sp.wallet_address = ?
        ORDER BY sp.sim_number DESC LIMIT 1
    """, (wallet,)).fetchone()

    # Change history
    changes = conn.execute("""
        SELECT * FROM wallet_changes WHERE wallet_address = ? ORDER BY change_date
    """, (wallet,)).fetchall()

    profile = {
        'wallet': wallet,
        'pnl': dict(pnl) if pnl else None,
        'weekly_pnl': weekly,
        'sim_profile': dict(sim_profile) if sim_profile else None,
        'change_history': [dict(c) for c in changes] if changes else [],
    }

    # Flags
    if pnl and pnl['incomplete_positions'] > 0:
        profile['warning'] = (
            f"P&L may be understated — {pnl['incomplete_positions']} positions excluded "
            f"(missing buy data)"
        )

    if sim_profile and sim_profile['profile_complete'] == 0:
        profile['sim_warning'] = "Sim profile is incomplete — DRL/SUM data was missing or broken"

    return profile


def format_wallet_for_prompt(profile):
    """Format a wallet profile as text for the evaluation prompt."""
    w = profile['wallet']
    lines = [f"\n### Wallet {w[:10]}...{w[-4:]}"]

    pnl = profile.get('pnl')
    if pnl:
        lines.append(f"Game (actual): {pnl.get('game', 'UNKNOWN')} | "
                      f"Trades: {pnl.get('total_trades', 0)} | "
                      f"Markets: {pnl.get('unique_markets', 0)}")
        lines.append(f"Realized P&L: ${pnl.get('realized_pnl', 0):,.2f} | "
                      f"Unrealized: ${pnl.get('unrealized_invested', 0):,.2f} open | "
                      f"Invested: ${pnl.get('total_invested', 0):,.2f}")
        if pnl.get('first_trade') and pnl.get('last_trade'):
            lines.append(f"Active: {pnl['first_trade'][:10]} to {pnl['last_trade'][:10]}")

    # Weekly P&L
    weekly = profile.get('weekly_pnl', [])
    if weekly:
        lines.append("Weekly P&L:")
        for w_data in weekly:
            resolved_str = f"{w_data['markets_resolved']} resolved"
            unreal_str = f", ${w_data['unrealized']:,.0f} open" if w_data['unrealized'] > 0 else ""
            lines.append(f"  {w_data['week_start']}: "
                          f"{'+'if w_data['realized_pnl']>=0 else ''}"
                          f"${w_data['realized_pnl']:,.0f} ({resolved_str}{unreal_str})")

    # Sim profile
    sim = profile.get('sim_profile')
    if sim:
        lines.append(f"Sharp Sim #{sim.get('sim_number', '?')}:")
        lines.append(f"  Sim PnL: ${sim.get('sim_pnl', 0):,.2f} | "
                      f"ROI: {(sim.get('sim_roi_pct', 0) or 0) * 100:.1f}%")
        lines.append(f"  Median entry: {sim.get('median_entry_price', 0):.3f} | "
                      f"P&L concentration (top1): {(sim.get('pnl_concentration_top1', 0) or 0) * 100:.0f}%")
        flags = []
        if sim.get('has_arb_pattern'):
            flags.append("ARB_PATTERN")
        if sim.get('has_scalp_pattern'):
            flags.append("SCALP_PATTERN")
        if sim.get('pnl_concentration_top1', 0) and sim['pnl_concentration_top1'] > 0.5:
            flags.append("HIGH_CONCENTRATION")
        if flags:
            lines.append(f"  Flags: {', '.join(flags)}")

    # Warnings
    if profile.get('warning'):
        lines.append(f"⚠️ {profile['warning']}")
    if profile.get('sim_warning'):
        lines.append(f"⚠️ {profile['sim_warning']}")

    # Change history
    changes = profile.get('change_history', [])
    if changes:
        lines.append(f"Change history: {len(changes)} events")
        for c in changes[-3:]:  # last 3
            lines.append(f"  {c['change_date']}: {c['action']} ({c.get('game_filter', '?')})")

    return '\n'.join(lines)
